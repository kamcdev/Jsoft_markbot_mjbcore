### Jsoft_markbot_mjbcore

 <img src="https://www.jsoftstudio.top/css/Jsoft_logo.png" width = "100" height = "100" alt="Jsoft_logo" align=center />

###### ©2024-2026 Jsoft Studio

------

<img src="https://img.shields.io/github/stars/kamcdev/Jsoft_markbot_mjbcore.svg">

<img src="https://img.shields.io/badge/Python-3.13.7-blue">

<img src="https://img.shields.io/badge/交流QQ群-984242265-purple">

<img src="https://img.shields.io/badge/B站-J软件官方-light">

<img src="https://img.shields.io/badge/官网-www.jsoftstudio.top-yellow">

<img src="https://img.shields.io/badge/使用提示-生产环境建议使用venv虚拟环境-red">

------

目录
* [介绍](#介绍)
* [部署](#部署)
    * [克隆项目文件](#克隆)
    * [准备环境](#准备)
    * [启动项目](#启动)
* [结语](#结语)

<p id="介绍"></p>

------

# 介绍

这是一款使用OneBot11客户端，基于Python开发的QQ机器人框架

可自行开发插件扩展功能

成品演示：加入上方提到的交流QQ群即可使用

<p id="部署"></p>

------

# 部署

<p id="克隆"></p>

1.克隆项目文件

使用git工具命令

```
git clone https://github.com/kamcdev/Jsoft_markbot_mjbcore.git
```

或

直接下载压缩包

<p id="准备"></p>

2.准备环境

安装Python3并在安装过程中启用环境变量

进入mjbcore目录

使用命令

```
pip install -r requirements.txt
```

安装预设的依赖列表

随后在group.json进行bot配置

在完成测试前建议打开测试模式避免被其他用户误用

<p id="启动"></p>

3.启动项目

使用命令

```
python bot_v1_1.0.3.py
```

启动bot程序

可在浏览器输入[http://127.0.0.1:34343](http://127.0.0.1:34343)

进入webui性能面板

待配置和测试完毕后，即可关闭测试模式并开放运行

功能插件开发可见[命令注册与模块开发文档.md](命令注册与模块开发文档.md)

<p id="结语"></p>

------

# 结语

感谢您的体验与支持，希望您在体验便利的同时也可以贡献一份代码，为本项目的开源事业做出贡献！