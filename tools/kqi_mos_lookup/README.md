# KQI—MOS 带宽查表生成器

该目录保存 `KQI_MOS带宽查表_v1.xlsx` 的完整生成代码。工作簿中的 MOS 保守值、典型值、乐观值和 MOS 等级均写成 Excel 公式，不是静态结果。

如果重点是求解器需要的细粒度对应关系，而不是 Excel 展示，请运行：

```powershell
python .\tools\kqi_mos_lookup\generate_mos_kqi_relation.py
```

它会在 `outputs/9_20_1/mos_kqi_relation_v2/` 下生成正式使用的两个目录：

- `solver_lookup/` 下保存博弈和暴搜直接使用的0.1 Mbps正向动作表；
- `mos_choice_typical/` 下每个目标MOS只保留一个最小带宽动作；
- 对插值规则、可达范围和使用限制的 Markdown 说明。

需要重新生成调试和审计中间表时，显式增加 `--include-audit`。

在仓库根目录运行：

```powershell
.\tools\kqi_mos_lookup\generate_lookup.ps1
```

自定义输出位置：

```powershell
.\tools\kqi_mos_lookup\generate_lookup.ps1 `
  -Output "outputs/9_20_1/KQI_MOS带宽查表_v2.xlsx" `
  -PreviewDir "outputs/9_20_1/KQI_MOS带宽查表_v2_previews"
```

文件说明：

- `generate_lookup.mjs`：KQI 区间、0.1 Mbps 网格、MOS Excel 公式、格式、校验和导出逻辑。
- `generate_lookup.ps1`：使用 Codex 工作区自带的 Node.js 和 `@oai/artifact-tool` 运行生成器。
- `generate_mos_kqi_relation.py`：生成求解器可直接读取的正向与反向细粒度关系。
- `公式说明.md`：与当前 `game_solving/models/mos.py` 对齐的数学公式、角点规则和已知限制。

生成器覆盖 8 类业务：开直播、看直播、视频、短视频、云游、视频会议、视频通话和手游。看直播沿用开直播规则并把媒体方向改为下行；视频和短视频使用 0～1、1～5、5～12 Mbps 码率档；短视频没有分辨率输入，MOS质量项只使用码率。
