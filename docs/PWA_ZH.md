# 安卓平板安装与全屏启动

前端公开 `public/manifest.webmanifest`，根页面通过 `rel="manifest"` 引用。
复用现有 192×192、512×512 图标，应用标识、启动地址和 scope 均为 `/`，
`display` 设置为 `fullscreen`。不强制横竖屏，遵循设备旋转设置。

## 安装

1. 部署本次前端构建，在安卓平板的 Chrome 中打开 **HTTPS** 网站。
2. 在 Chrome 菜单选择“安装应用”或“添加到主屏幕”，按提示完成安装。
3. 从平板主屏幕的 **Piano KT 图标**启动，进入应用窗口；普通浏览器标签页仍显示浏览器界面。
4. 检查首页、登录、曲目选择、演奏及页面切换均在同一应用窗口中；旋转平板检查布局。

浏览器安装提示可能需要访问并交互一段时间才出现。电脑 localhost 可以用于开发检查，
平板访问 `http://电脑局域网IP:端口` 不等同于 localhost；安装验收应使用 HTTPS 地址。

全屏是浏览器首选显示模式，最终效果取决于安卓版本、浏览器及设备设置；不支持时按标准
回退到 standalone 等模式。系统手势仍可临时唤出导航栏，这不是锁定设备的 kiosk 模式。

这是在线安装版：没有注册 Service Worker 或添加离线缓存；安装不代表可以离线加载、
登录或上传。现有演奏恢复队列仍按原有逻辑工作。

## 开发验证

```bash
npm run check-types
npm run build
```

检查构建产物 `build/client/index.html` 包含 Manifest 引用，
`build/client/manifest.webmanifest`、`icon-192.png` 和 `icon-512.png` 均存在。
部署后访问 `/manifest.webmanifest` 应返回 JSON，而不是 SPA 的 HTML 回退页面。
Chrome DevTools 的 Application → Manifest 可检查名称、图标和显示模式。
已安装版本如未更新显示模式，可先删除主屏幕应用入口再从网站重新安装；无需清除网站数据。

参考：[Chrome 安装条件](https://web.dev/articles/install-criteria)、
[Manifest display 与回退规则](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/display)。
