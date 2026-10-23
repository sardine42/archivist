# 留言信箱

每页右下角的信封按钮打开私有留言表单。默认每条最多 500 个 Unicode 字符，最多保留 200 条。
信箱满后拒绝新留言，不会覆盖旧留言；删除后立即恢复容量。留言不会公开显示。

## 配置

在不受 Git 管理的 `config.local.yaml` 中覆盖默认值：

```yaml
mailbox:
  enabled: true
  max_length: 500
  max_messages: 200
  database: archive/mailbox/inbox.sqlite3
  public_url: https://example.com
  allowed_origins:
    - https://example.com
    - https://www.example.com
```

`database` 相对项目根目录解析。数据库必须位于静态网站根目录以外，且不进入 Git。
服务也支持 `ARCHIVIST_MAILBOX_DB`、`ARCHIVIST_MAILBOX_PUBLIC_URL`、
`ARCHIVIST_MAILBOX_MAX_LENGTH`、`ARCHIVIST_MAILBOX_MAX_MESSAGES` 环境变量。
需要多个公开域名时，使用显式的 `allowed_origins` 列表或逗号分隔的
`ARCHIVIST_MAILBOX_ALLOWED_ORIGINS`；未配置时仅允许 `public_url`。

## 私有查收与清理

在保存数据库的机器上运行，或者通过已有的安全远程管理渠道执行：

```bash
.venv/bin/archivist mailbox status
.venv/bin/archivist mailbox list
.venv/bin/archivist mailbox read <id>
.venv/bin/archivist mailbox delete <id>
.venv/bin/archivist mailbox clear
```

独立安装的服务可用 `python -m archivist.mailbox_cli` 代替 `archivist mailbox`。
查看各子命令的 `--help` 可获取 JSON 输出、确认等选项。网站没有公开的读取、删除或管理 API。

## 生产服务

静态构建不会启动留言服务。使用 Python 3.12+ 为服务创建虚拟环境，安装项目依赖及
Gunicorn，再由进程管理器运行独立的 Flask 工厂：

```bash
uv sync
uv pip install --python .venv/bin/python 'gunicorn>=23,<24'
.venv/bin/gunicorn --bind 127.0.0.1:8781 --workers 2 --threads 2 \
  'archivist.web.mailbox:create_mailbox_app()'
```

服务只暴露配置和投递端点，不加载论文、文档或管理页面。配置中指定固定的公开 URL，
服务会验证投递请求的 Origin。请求正文最多 8 KiB，支持隐藏的垃圾留言诱捕字段，
同一客户端每 10 分钟最多提交 3 条。节流状态跨进程共享，客户端地址经带密钥的摘要处理后保存。

Nginx 在主源站添加以下位置块。只在可信的本地代理后设置
`ARCHIVIST_MAILBOX_TRUST_PROXY=true`，并由代理覆盖转发头；不要直接公开 Gunicorn 端口。

```nginx
location ^~ /api/mailbox/ {
    client_max_body_size 8k;
    proxy_pass http://127.0.0.1:8781;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 15s;
    proxy_hide_header Cache-Control;
    add_header Cache-Control "no-store" always;
}
```

## 多源站

所有投递请求必须固定到同一个主服务，容量限制才是全站共享的。现有地区回源 Worker
将所有 POST 请求交给主源站，静态页面仍可按地区分流。

构建会把公开配置写入 `api/mailbox/config`，供其他静态源站提供。次源站可配置：

```nginx
location = /api/mailbox/config {
    default_type application/json;
    try_files $uri =404;
    add_header Cache-Control "no-store" always;
}
location ^~ /api/mailbox/ {
    add_header Cache-Control "no-store" always;
    return 405;
}
```

修改上限或关闭信箱时，同时更新主服务配置、重启服务，并重新构建部署到所有静态源站。
普通静态站部署只同步公开配置和前端资源，不同步或清空数据库。后端代码更新需要另行
发布服务代码并重启进程。备份数据库时使用 SQLite 的一致性备份接口；不在 Git 中保存备份。
