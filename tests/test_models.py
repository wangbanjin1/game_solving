import math
import unittest
from experience_solver.config import load_config, validate
from experience_solver.mos_model import MosModel, bitrate_score, rtt_score, loss_score, stall_score, clip
from experience_solver.bandwidth_model import BandwidthModel
from simulation_generator.population import counts
from simulation_generator.range_catalog import classify
from experience_solver.schemas import multiplier


class ModelsTest(unittest.TestCase):
    def setUp(self):
        self.c = load_config()
        self.bw, self.mos = BandwidthModel(self.c), MosModel(self.c)
        self.k = {'bitrate_kbps':2000,'resolution':720,'rtt_ms':80,'loss_ratio':.001,'stall_ratio':.01}

    def test_original_endpoints_and_invalid(self):
        self.assertEqual(bitrate_score(0),2.5)
        for f in (rtt_score,loss_score,stall_score):
            self.assertEqual(f(0),5)
        for x in (-1,math.nan,math.inf,2,True):
            with self.assertRaises(ValueError):
                loss_score(x)

    def test_document_roundtrip_example(self):
        b = self.bw.inverse(self.k)
        self.assertAlmostEqual(b,3.581503,places=6)
        f = self.bw.forward(b,{'bitrate_kbps':2000,'resolution':720})
        for key in ('rtt_ms','loss_ratio','stall_ratio'):
            self.assertLessEqual(f[key],self.k[key]+1e-12)
        self.assertAlmostEqual(self.mos.evaluate(f,'P4'),4.510347,places=6)

    def test_formula_groups_independent_values(self):
        groups = {'P1':(.25,.05,.25,.1),'P2':(.25,.05,.05,.25),'P3':(.04,.25,.04,.25),'P4':(.25,.05,.05,.25),'P5':(0,0,.25,.04)}
        sb, sr = 5/(1+math.exp(-2000/928.984)),5/(1+math.exp(-720/410))
        sl, ss = 4*math.exp(-180.94*.001)+1,4.96
        for group,(w1,w2,g1,g2) in groups.items():
            q = 4.5 if group == 'P5' else clip(5-4*(w1*(5-sb)+w2*(5-sr)))
            v = clip(5-4*(g1*(5-sl)+g2*(5-ss)))
            expected = (q+4*math.exp(-.0035*80)+1+v)/3
            self.assertAlmostEqual(self.mos.evaluate(self.k,group),expected)

    def test_floors_monotonic_and_zero_amplitude(self):
        with self.assertRaisesRegex(ValueError,'UNREACHABLE'):
            self.bw.inverse({**self.k,'rtt_ms':30})
        a = self.bw.inverse({**self.k,'loss_ratio':.0002})
        b = self.bw.inverse({**self.k,'loss_ratio':.00011})
        self.assertGreaterEqual(b,a)
        first = self.bw.forward(4,self.k)
        second = self.bw.forward(5,self.k)
        for key in ('rtt_ms','loss_ratio','stall_ratio','buffer_ms'):
            self.assertLessEqual(second[key],first[key])
        self.c['bandwidth']['loss_amp'] = 0
        self.assertGreater(self.bw.inverse({**self.k,'loss_ratio':.0001}),0)

    def test_missing_metrics_and_phase(self):
        k = {**self.k,'buffer_ms':5000}
        self.assertNotEqual(self.mos.evaluate(k,'P4','initial'),self.mos.evaluate(k,'P4','steady'))
        self.assertEqual(self.mos.evaluate({**k,'jitter_ms':40},'P5'),self.mos.evaluate({**k,'jitter_ms':0},'P5'))
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT'):
            self.mos.evaluate({**k,'loss_ratio':None,'stall_ratio':None},'P4')

    def test_counts_boundaries_multiplier(self):
        self.assertEqual(sum(counts(30,{'a':.333,'b':.333,'c':.334}).values()),30)
        self.assertEqual(classify(.0015,.0015,.005),'good')
        self.assertEqual(classify(math.nextafter(.0015,1),.0015,.005),'average')
        self.assertEqual(multiplier({'sample_count':10,'inclusion_probability':.1}),100)

    def test_config_rejects_bad_probabilities_and_scales(self):
        self.c['population']['package_probs']['vip'] = .4
        with self.assertRaises(ValueError):
            validate(self.c)
        self.c = load_config()
        self.c['bandwidth']['efficiency'] = 0
        with self.assertRaises(ValueError):
            validate(self.c)


if __name__ == '__main__':
    unittest.main()
