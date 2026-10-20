# 同步部署到多个源站

在不受 Git 管理的 `config.local.yaml` 中设置主源站和附加源站：

```yaml
deploy:
  host: deploy@primary.example.com
  extra_hosts:
    - deploy@secondary.example.com
  remote_site_path: "~/site"
  remote_archive_path: "~/archive"
```

各源站使用相同的站点和 archive 目录布局，并提前配置 SSH 访问与 Web 服务。

```bash
.venv/bin/archivist deploy
```

命令只构建一次，然后按主源站、附加源站的顺序同步静态站点、论文图片和元数据。重复源站只同步一次。PDF 不会传输。

只有全部源站的同步都成功才会输出 `Deploy complete.`。任何一次同步失败都会返回非零退出码并指出失败源站，已完成的同步不会回滚。

需要只更新指定源站时使用 `--host`，它会覆盖主源站和全部附加源站：

```bash
.venv/bin/archivist deploy --host deploy@secondary.example.com
```

`--skip-build` 沿用现有构建结果，仍会同步所有配置的源站；与 `--host` 合用时只同步指定源站。

## Cloudflare 按地区回源

使用 [`scripts/cloudflare/geo-origin.js`](../scripts/cloudflare/geo-origin.js) 作为 Cloudflare Module Worker。示例域名为 `example.com`：中国大陆的 GET、HEAD 请求优先访问阿里云源站，其他请求访问现有主源站。阿里云连接、TLS、等待响应头超过 8 秒或返回 5xx 时，Worker 会回退主源站；401、403、404、410 不会触发回退。响应正文保持流式传输，开始返回正文后不会切换源站。

### 源站准备

- 两个源站提供同一份构建结果、论文图片和所需元数据。先完成同步与验证，再启用回源分流；后续使用多源站部署保持一致。
- 公开 HTML、所有 JSON 及整个 `/search/` 路径使用 `Cache-Control: no-cache, max-age=0, must-revalidate`，每次使用前检查是否更新；保留 `ETag`、`Last-Modified` 和条件请求支持，内容未变化时可返回 304，避免重复传输正文。自有 CSS、JavaScript 使用模板生成的内容版本参数，和公开图片一起保留静态资源缓存；不要给未带版本的搜索入口脚本设置长期 immutable 缓存。
- 两个源站都需要为 `example.com`、`www.example.com` 提供有效 HTTPS 证书，并保持 Cloudflare **Full (strict)**。Worker 保留原始 URL 和 Host，只改变解析到的源站。
- 两个源站都应强制文档页面及图片鉴权，文档响应使用 `Cache-Control: private, no-store`，旧 PDF 下载接口返回 410。鉴权文件和密钥通过受限方式单独管理，禁止写入仓库。
- 源站仅允许 Cloudflare 的真实连接地址访问网站端口，并正确维护 Cloudflare IP 地址范围；不能仅凭来访请求中的转发头判断是否可信。
- 明确证书续期与同步责任。脚本将 `/.well-known/acme-challenge/` 固定交给主源站，避免 HTTP-01 验证按访问地区落到不同机器。若两个源站共用证书，续期后必须安全同步并验证、重载两边的 Web 服务；若独立签发证书，需要使用 DNS 验证或另行设计挑战文件共享流程。

### Cloudflare 配置

1. 保留 `example.com`、`www.example.com` 当前指向主源站的代理 DNS 记录。新增同一 zone 内的 **DNS-only** A 记录 `aliyun-origin.example.com`，指向阿里云源站。`resolveOverride` 必须使用同 zone 的主机名，不能直接填写 IP。
2. 创建 Worker，粘贴脚本，并配置两个文本变量：

   | 变量 | 示例值 |
   | --- | --- |
   | `PUBLIC_HOST` | `example.com` |
   | `ALIYUN_ORIGIN` | `aliyun-origin.example.com` |

3. 兼容日期使用当前日期，且不得早于 `2024-11-11`，以支持 `cache: "no-store"`。
4. **Settings → Runtime → Cache 保持 Disabled**。这个开关控制执行 Worker 之前的整份响应缓存；启用后，命中缓存的请求会跳过地区判断，而且该层缓存不受 zone Cache Rules 管理。关闭它不会关闭脚本中 `fetch()` 子请求沿用的网站缓存。
5. 仅添加下面两条精确主机名的 Route，并将每条 Route 设为 **Fail open**。不要使用涵盖源站别名的域名通配路由。

   ```text
   example.com/*
   www.example.com/*
   ```

   免费额度耗尽等情况下，Fail open 会绕过 Worker，继续按当前 DNS 访问主源站。源站本身仍负责文档鉴权与 PDF 禁用。

6. 将 **Caching → Configuration → Browser Cache TTL** 设为 **Respect Existing Headers**，避免全局浏览器 TTL 把源站要求立即验证的内容延长缓存。停用旧的公开 HTML、JSON 固定 TTL 规则，并检查其他规则没有对这些内容覆盖浏览器 TTL。
7. 最后一条 Cache Rule 使用 **Bypass cache**，覆盖所有非静态文件、整个 `/search/` 路径、`/docs` 与 `/docs/` 下的内容、旧 PDF 下载路径及携带 Authorization 的请求。这条规则也保证 Worker 因额度或异常而 Fail open 时，页面和数据仍不复用旧边缘缓存。保留公开静态资源缓存，不要因文件扩展名而让文档图片或搜索脚本绕过上述排除项。

Worker 对相同范围的子请求设置 `cache: "no-store"`，不读取或写入 Cloudflare 子请求缓存；源站返回的公开页面和数据响应仍使用 `no-cache`，允许浏览器保存正文、每次验证。这里的两种设置分别控制边缘缓存和浏览器缓存，文档响应继续使用更严格的 `private, no-store`。

公开静态资源命中 Cloudflare 缓存时无需访问任何源站。两个源站使用同一公开 URL 和缓存键，因此静态资源的地区分流是在需要回源时选择服务器；不能用一次缓存命中的响应判断实际回源位置。页面、JSON 和搜索资源则每次回源验证，发布后不必等待较长的边缘 TTL。

### 验证

启用前备份源站配置，记录 Worker 版本和两条 Route。先分别验证源站 HTTPS、站点内容、鉴权和 PDF 禁用，再验证 Cloudflare 链路。

- 从国内网络与海外网络分别请求正式域名，带各自唯一的查询参数；同时检查两台源站访问日志，确认国内请求由阿里云处理，海外请求由主源站处理。无需公开调试响应头、测试路径或强制地区参数；不能用客户端自填的国家请求头代替真实地区验证。
- 验证根域名与 `www`、HTTP 到 HTTPS 跳转、公开页面和图片。命中缓存的请求不应要求源站出现新日志；需要证明回源时使用新的查询参数，并确认该请求未命中缓存。
- 连续请求同一页面、JSON 和搜索入口地址，确认没有旧缓存 HIT，且浏览器收到 `no-cache, max-age=0, must-revalidate`。用首个响应的 ETag 发起 `If-None-Match` 请求，确认未变化的内容可以返回 304，状态、验证头和缓存控制头均正确；同时确认公开图片和带内容版本的 CSS、JavaScript 仍可缓存。最后部署一次实际更新，核对两源站和正式域名显示相同的新内容。
- 匿名访问 `/docs/` 及实际存在的文档图片都应返回 401；旧 `/reading/2026/sample-paper/pdf` 形式的下载路径应返回 410。两类响应都不得进入共享缓存。使用有效文档账号验证正常授权访问，检查两边权限一致。
- 验证 `/.well-known/acme-challenge/` 无论访问地区都交给主源站，并完成证书续期演练。
- 在受控测试中让阿里云源站返回 5xx 或暂时不可用，确认请求回退主源站；恢复后确认国内请求重新使用阿里云。401、403、404、410 应保留原状态，不能因回退而掩盖权限或内容差异。

### 回滚

发生异常时，先移除两条 Worker Route，或将其取消绑定 Worker。根域名与 `www` 的 DNS 始终保留主源站，因此移除路由即可恢复原来的回源路径。再次验证公开页面正常、文档匿名访问 401、旧 PDF 返回 410。

若仅需回滚 Worker 代码，可恢复记录的上一版本；同时核对变量、Runtime Cache 和 Route 设置。新增的源站别名在确认没有其他使用方后可以删除。无需为回滚公开内容而放开源站访问限制或关闭鉴权。

官方参考：[Request 与 resolveOverride](https://developers.cloudflare.com/workers/runtime-apis/request/)、[Fetch 子请求缓存设置](https://developers.cloudflare.com/workers/runtime-apis/fetch/)、[子请求与网站缓存](https://developers.cloudflare.com/workers/reference/how-the-cache-works/)、[Browser Cache TTL](https://developers.cloudflare.com/cache/how-to/edge-browser-cache-ttl/)、[Cache-Control](https://developers.cloudflare.com/cache/concepts/cache-control/)、[Workers Cache 配置](https://developers.cloudflare.com/workers/cache/configuration/)、[请求额度与 Fail open](https://developers.cloudflare.com/workers/platform/limits/)。

浏览器可能已保存按旧响应头仍然有效的页面，清除 Cloudflare 缓存无法删除这些本地副本。切换策略后，对已受影响的浏览器执行一次强制刷新（macOS：`Command + Shift + R`；Windows/Linux：`Ctrl + Shift + R`），或清除此站点的缓存；收到新响应头后，日常刷新即可重新验证最新内容。
