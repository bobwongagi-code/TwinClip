# Reference Graph Example

This is an end-to-end example of the reference-graph shape for one product
bundle. It is documentation, not a universal six-node template and not a
production scoring result.

The example assumes that the Storyboard completely covers the source replay
described by the breakdown video. The Storyboard therefore supplies the node
backbone, while the seven explicitly taught mechanisms are mounted on one or
more nodes. The same teaching point can span nodes when its function depends on
an earlier setup and a later payoff.

```json
{
  "reference_id": "hapsode_cleanser_v1",
  "backbone_source": "storyboard",
  "nodes": [
    {
      "node_id": "N1",
      "stage": "Hook",
      "time": "0-8s",
      "storyboard_summary": "展示油性问题肌和吸油纸，画面转到清洁后更亮的肌肤",
      "teaching_points": ["TP1_痛点钩子", "TP2_结果预告"]
    },
    {
      "node_id": "N2",
      "stage": "使用/演示",
      "time": "8-13s",
      "storyboard_summary": "涂抹洁面并搓出丰富泡沫",
      "teaching_points": ["TP3_真实使用"]
    },
    {
      "node_id": "N3",
      "stage": "产品/卖点",
      "time": "13-21s",
      "storyboard_summary": "剪开双管包装，展示内部结构和混合质地",
      "teaching_points": ["TP4_证据构建"]
    },
    {
      "node_id": "N4",
      "stage": "效果/证据",
      "time": "22-31s",
      "storyboard_summary": "再次展示起泡清洁过程，触摸呈现干净肌肤",
      "teaching_points": ["TP3_真实使用", "TP5_效果证明"]
    },
    {
      "node_id": "N5",
      "stage": "产品/卖点",
      "time": "32-50s",
      "storyboard_summary": "作为泥膜使用，等待 2-3 分钟，特写鼻部黑头去除效果",
      "teaching_points": ["TP5_效果证明", "TP6_购买理由"]
    },
    {
      "node_id": "N6",
      "stage": "CTA",
      "time": "51-52s",
      "storyboard_summary": "手持包装并指向购物车入口",
      "teaching_points": ["TP7_转化收口"]
    }
  ],
  "teaching_points": [
    {
      "id": "TP1_痛点钩子",
      "mounted_on": ["N1"],
      "description": "问题皮肤近景、吸油纸和濒临放弃的字幕"
    },
    {
      "id": "TP2_结果预告",
      "mounted_on": ["N1"],
      "description": "较早展示清洁后的皮肤，形成前后反差"
    },
    {
      "id": "TP3_真实使用",
      "mounted_on": ["N2", "N4"],
      "description": "上脸揉洗并产生丰富泡沫，跨节点强化",
      "cross_node": true
    },
    {
      "id": "TP4_证据构建",
      "mounted_on": ["N3"],
      "description": "剪开包装近拍膏体和内部结构，用实物证明而非口头宣称"
    },
    {
      "id": "TP5_效果证明",
      "mounted_on": ["N4", "N5"],
      "description": "清洗前后对比加问题部位特写，跨节点强化",
      "cross_node": true
    },
    {
      "id": "TP6_购买理由",
      "mounted_on": ["N5"],
      "description": "洁面和三分钟泥膜双用途，强调比传统泥膜更快"
    },
    {
      "id": "TP7_转化收口",
      "mounted_on": ["N6"],
      "description": "产品正面特写加购买入口指向"
    }
  ]
}
```

Before scoring creator videos, an operator should confirm the node meanings,
teaching-point boundaries, source timestamps, and the `mounted_on` links against
the actual breakdown and Storyboard. A replayed source clip may verify the
breakdown commentary, but it must not create a second copy of a teaching point.

The production report uses the stricter fields in
[`report-schema.md`](report-schema.md): source locators, minimum observable
evidence, false-positive guards, Storyboard node IDs, and explicit graph
relationships. This compact example is only meant to make the graph topology
and cross-node mounting easy to inspect.
