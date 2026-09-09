import copy
import tempfile
import unittest
from pathlib import Path
from experience_solver.config import load_config
from experience_solver.solver import Solver
from experience_solver.candidates import CandidateBuilder
from experience_solver.schemas import violations, multiplier
from experience_solver.coordinator import ResourceCoordinator
from simulation_generator.generator import DatasetGenerator
from simulation_generator.reference import universe,exact_reference,metrics
from simulation_generator.cli import main,read_lines


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = load_config('configs/tiny.json')
        cls.scenes,cls.inputs,cls.labels,cls.report = DatasetGenerator(cls.c).generate()

    def test_reproducible_generation(self):
        again = DatasetGenerator(self.c).generate()
        self.assertEqual(again[0],self.scenes)
        self.assertEqual(self.report['failed'],0)
        for s in self.inputs:
            for r in s['roles']:
                self.assertNotIn('kqi_final',r)
                self.assertNotIn('target_bandwidth_mbps',r)

    def test_solver_max_steps_and_determinism(self):
        c = copy.deepcopy(self.c)
        c['solver']['max_iterations'] = 1
        s = copy.deepcopy(self.inputs[0])
        before = copy.deepcopy(s)
        a,b = Solver(c).solve(s),Solver(c).solve(s)
        self.assertEqual(a['status'],'MAX_ITERATIONS')
        self.assertEqual(a['iterations'],1)
        self.assertEqual(a['role_decisions'],b['role_decisions'])
        self.assertEqual(s,before)
        self.assertEqual(a['constraint_violations'],[])

    def test_joint_oracle_and_wgr(self):
        s = self.inputs[0]
        pools = universe(s,self.c)
        ref = exact_reference(s,pools,self.c)
        self.assertEqual(ref['reference_status'],'exact_discrete')
        result = Solver(self.c).solve(s,candidate_universe=pools)
        m = metrics(s,result,ref,self.c)
        self.assertLessEqual(result['objective_H_total'],ref['H_reference']+1e-9)
        self.assertLessEqual(m['U_algorithm'],ref['U_reference']+1e-9)
        self.assertEqual(m['N_improved']+m['N_worsened']+m['N_unchanged'],3)

    def test_timeout_returns_feasible_floor(self):
        c = copy.deepcopy(self.c)
        c['solver']['time_budget_ms'] = 0
        result = Solver(c).solve(self.inputs[0])
        self.assertEqual(result['status'],'TIME_BUDGET')
        self.assertEqual(result['iterations'],0)
        self.assertEqual(result['constraint_violations'],[])
        self.assertEqual(len(result['role_decisions']),3)

    def test_hard_floor_infeasible(self):
        s = copy.deepcopy(self.inputs[0])
        s['bandwidth_total_mbps'] = .01
        s['unmanaged_mbps'] = s['safety_mbps'] = 0
        result = Solver(self.c).solve(s)
        self.assertEqual(result['status'],'INFEASIBLE_HARD_FLOOR')
        self.assertEqual(result['role_decisions'],[])
        self.assertGreater(result['capacity_gap_mbps'],0)

    def test_media_control_disabled_and_anchor_preserved(self):
        r = self.inputs[0]['roles'][0]
        pool = CandidateBuilder(self.c).build(r)
        self.assertTrue(any(a['is_anchor'] for a in pool))
        self.assertTrue(all(a['predicted_kqi']['bitrate_kbps'] == r['kqi_observed']['bitrate_kbps'] for a in pool))

    def test_strict_quota_and_fixed_capacity_failures(self):
        c = load_config('configs/strict_failure.json')
        scenes,_,_,report = DatasetGenerator(c).generate()
        self.assertEqual(scenes,[])
        self.assertIn('MOS_BAND_UNREACHABLE',report['failures'][0]['reason'])
        c = copy.deepcopy(self.c)
        c['cell']['capacity_mode'],c['cell']['capacity_mbps'] = 'fixed',.01
        self.assertEqual(DatasetGenerator(c).generate()[3]['failed'],2)

    def test_cli_serialized_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'run'
            self.assertEqual(main(['run','--config','configs/tiny.json','--output',str(out),'--max-steps','2']),0)
            self.assertEqual(len(read_lines(out/'solve_results.jsonl')),2)
            self.assertTrue((out/'validation_report.json').exists())

    def test_recovery_order_and_price_raw_demand(self):
        s = copy.deepcopy(self.inputs[0])
        s['roles'] = s['roles'][:2]
        for i,r in enumerate(s['roles']):
            r['current_profile_id'] = 'demo_non_gbr' if i == 0 else 'demo_gbr'
        def action(r,b,h,ident):
            return {'role_id':r['role_id'],'action_id':ident,'bandwidth_mbps':b,'H':h,'hard_constraints_satisfied':True,'baseline_satisfied':True,'is_anchor':False,'switch_cost':0,'predicted_mos':4,'profile_id':r['current_profile_id']}
        pools = [[action(r,1,0,f'{i}low'),action(r,3,1,f'{i}high')] for i,r in enumerate(s['roles'])]
        s['bandwidth_total_mbps'],s['unmanaged_mbps'],s['safety_mbps'] = 4,0,0
        repaired,_ = ResourceCoordinator(self.c).repair_and_upgrade([p[1] for p in pools],pools,s)
        self.assertEqual(repaired[0]['bandwidth_mbps'],1)
        self.assertEqual(repaired[1]['bandwidth_mbps'],3)
        c = copy.deepcopy(self.c)
        c['solver']['lambda_initial'] = 0
        c['solver']['max_iterations'] = 1
        result = Solver(c).solve(s,candidate_universe=pools)
        self.assertGreater(result['lambda_final'],0)
        self.assertEqual(result['trace'][0]['raw_demand_mbps'],6)
        self.assertEqual(result['allocated_mbps'],4)

    def test_user_cap_and_population_weight(self):
        s = copy.deepcopy(self.inputs[0])
        s['roles'][0]['sample_count'] = 10
        s['roles'][0]['inclusion_probability'] = .1
        s['bandwidth_total_mbps'] = 10000
        result = Solver(self.c).solve(s)
        self.assertEqual(result['role_decisions'][0]['population_weight'],100)
        self.assertAlmostEqual(result['allocated_mbps'],sum(multiplier(r)*a['bandwidth_mbps'] for r,a in zip(s['roles'],result['role_decisions'])))
        s['user_bandwidth_caps_mbps'] = {s['roles'][0]['user_id']:.01}
        self.assertEqual(Solver(self.c).solve(s)['status'],'INFEASIBLE_HARD_FLOOR')

    def test_mixed_businesses_end_to_end(self):
        c = load_config()
        c['num_scenes'] = 1
        c['solver']['max_iterations'] = 2
        scenes,inputs,labels,report = DatasetGenerator(c).generate()
        self.assertEqual(report['failed'],0)
        s = inputs[0]
        self.assertTrue(any(not r['is_key_business'] for r in s['roles']))
        result = Solver(c).solve(s)
        self.assertEqual(result['constraint_violations'],[])
        self.assertTrue(result['role_decisions'])
        for r,a in zip(s['roles'],result['role_decisions']):
            if not r['is_key_business']:
                self.assertIsNone(r['mos_observed'])
                self.assertIsNone(a['predicted_mos'])
                self.assertEqual(a['bandwidth_mbps'],r['qos_floor_mbps'])
            if r['package'] != 'normal':
                self.assertEqual(r['tolerance_observed'],'intolerant')

    def test_invalid_observed_kqi_rejected(self):
        s = copy.deepcopy(self.inputs[0])
        s['roles'][0]['kqi_observed']['loss_ratio'] = 2
        with self.assertRaises(ValueError):
            Solver(self.c).solve(s)

    def test_fixed_capacity_feasible_and_reference_budget(self):
        c = copy.deepcopy(self.c)
        c['cell']['capacity_mode'],c['cell']['capacity_mbps'] = 'fixed',100
        scenes,inputs,_,report = DatasetGenerator(c).generate()
        self.assertEqual(report['failed'],0)
        self.assertEqual(inputs[0]['bandwidth_total_mbps'],100)
        c['reference']['max_combinations'] = 1
        ref = exact_reference(inputs[0],universe(inputs[0],c),c)
        self.assertEqual(ref['reference_status'],'not_computed')
        self.assertNotIn('U_reference',ref)

    def test_no_profitable_move_leaves_slack(self):
        s = copy.deepcopy(self.inputs[0])
        s['bandwidth_total_mbps'] = 10000
        builder = CandidateBuilder(self.c)
        pools = [[next(a for a in builder.build(r) if a['is_anchor'])] for r in s['roles']]
        result = Solver(self.c).solve(s,candidate_universe=pools)
        self.assertEqual(result['status'],'CONVERGED_LOCAL')
        self.assertLess(result['allocated_mbps'],result['available_mbps'])
        self.assertEqual(result['lambda_final'],0)

    def test_generation_failure_cli_reports_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'failure'
            self.assertEqual(main(['generate','--config','configs/strict_failure.json','--output',str(out)]),2)
            self.assertTrue((out/'generation_report.json').exists())
            self.assertEqual(read_lines(out/'scenes.jsonl'),[])


if __name__ == '__main__':
    unittest.main()
