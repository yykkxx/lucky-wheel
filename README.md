# 🎡 幸运大转盘 Lucky Wheel

本地 Web 抽奖程序 —— 纯 Python 标准库零依赖，含转盘动画、粒子特效、库存与概率管理。

## ✨ 特性

- **零依赖** — 仅使用 Python 标准库，无需 `pip install`
- **转盘动画** — Canvas 实时渲染，缓动旋转、灯泡闪烁、指针抖动
- **粒子特效** — 中奖烟花、喷泉、光环扩散
- **音效合成** — WebAudio 实时合成，无需音频素材
- **库存管理** — 限量奖品自动扣减，抽完即止
- **概率引擎** — 服务端按权重实时抽取，前端仅展示
- **连接状态** — 实时在线/离线指示，断线自动重试
- **请求超时** — 所有 API 请求 8s 超时 + AbortController
- **PWA 支持** — 可安装到桌面/主屏幕
- **移动适配** — 响应式布局 + reduced-motion 无障碍
- **配置热更新** — 修改 `prizes.json` 后前端一键刷新

## 🚀 快速开始

```bash
cd lottery
python3 lottery_server.py              # 默认 8000 端口
python3 lottery_server.py -p 9000      # 指定端口
python3 lottery_server.py --host 0.0.0.0   # 局域网访问
python3 lottery_server.py --reset      # 清空记录后启动
python3 lottery_server.py --no-browser # 不自动打开浏览器
```

## 📁 文件结构

```
lottery/
├── lottery_server.py   # 服务端（HTTP + 抽奖引擎）
├── prizes.json         # 奖品配置
├── state.json          # 抽奖状态（自动生成）
├── README.md
└── web/
    └── index.html       # 前端页面（含 CSS + JS）
```

## 🎁 配置说明

编辑 `prizes.json`：

```json
{
  "title": "幸运大转盘",
  "subtitle": "转动转盘，好运即刻降临",
  "draw_limit": 0,
  "prizes": [
    {
      "name": "一等奖 · 蓝牙耳机",
      "icon": "🎧",
      "desc": "",
      "color": "#ff3d81",
      "weight": 0.5,
      "stock": 1
    },
    {
      "name": "谢谢参与",
      "icon": "🍀",
      "desc": "",
      "color": "#5a6b8c",
      "weight": 30,
      "stock": null
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 奖品名称 |
| `icon` | string | Emoji 图标 |
| `desc` | string | 奖品描述 |
| `color` | string | 扇形颜色（十六进制） |
| `weight` | number | 中奖权重（非概率，自动归一化） |
| `stock` | number/null | 库存数量，`null` 为不限量 |

## 🌐 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取奖品配置 + 库存状态 |
| POST | `/api/draw` | 执行抽奖 |
| GET | `/api/history` | 获取最近 50 条记录 |
| POST | `/api/reset` | 清空记录与库存 |
| GET | `/api/health` | 健康检查 |

## 📱 PWA

本应用支持添加到主屏幕。在 Chrome 中访问后，地址栏会出现安装按钮，或在菜单中选择"安装应用"。

## 📄 License

MIT
