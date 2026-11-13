# 账号、会话与项目成员

Web 首页读取服务端会话。未登录时用已开通邮箱申请验证码，验证成功后回到原项目或邀请。退出当前浏览器会话后可以换账号。独立的试点演示入口使用公开快照与模拟执行记录。实现与验收对应 [#3](https://github.com/lkun-lea/myagentdesk/issues/3)。

## 身份与操作回执

- Supabase Auth 负责发码、验码、核对已确认的用户、刷新和退出。MyAgentDesk 用核验后的 Supabase 用户 UUID 建立身份，使用邮箱展示身份和匹配邀请。发码关闭自动开户。验证码输错和过期共用 Supabase 的错误结果，页面提示核对最新邮件。
- 浏览器只获得不透明的 HttpOnly 会话 Cookie。HTTPS 使用 Secure Cookie，开发 HTTP 仅允许环回地址。写请求检查 Origin 和专用请求头。上游令牌仅在服务端使用，在 PostgreSQL 内加密保存。刷新按原会话串行核验，失效时回到登录页，断连时显示可重试状态。
- 发码和验码由浏览器登录流程及操作编号绑定。原请求重试查询同一回执。回执丢失时可在原浏览器核对，已成功的验码可以恢复原会话。Provider 的处理结果不明时显示该状态，并由用户明确选择下一步。退出撤销当前工作台会话，保留其他设备会话。远端撤销失败时显示待确认。

## 项目权限

- 项目属于工作区。账号创建的首个项目建立个人工作区，后续项目复用该工作区。共享项目通过项目成员关系访问，工作区归属只授予工作区内的建项权限。
- 所有者可邀请、撤销邀请、调整成员角色和移除成员。管理员可邀请、撤销邀请和移除普通成员。普通成员可查看授权项目和成员。邀请是绑定邮箱的限期链接，交由项目负责人分享。受邀邮箱须已在 Supabase 开通。重复接受返回已有成员关系。撤权后旧邀请无法恢复权限。
- 列表、详情、活动订阅和下载各自核验当前会话和项目权限。API 响应使用 `no-store`。活动流按事件游标在重连时重查权限。前端在身份变化时卸载旧订阅及数据，项目页也会定期核对权限。

## 配置真实邮箱验收

1. 在 Supabase 创建一个 MyAgentDesk 专用测试项目。到 **Authentication → Users** 预建两个已确认的测试用户。使用你能收信的测试邮箱。项目邀请只授予已开通账号的项目权限。
2. 在 **Authentication → Email Templates** 的 **Magic Link** 模板中保留 `{{ .Token }}`，让邮件显示六位验证码。按邮箱的投递范围配置 **Custom SMTP**，再用真实收件箱核对到达情况。[邮箱验证码](https://supabase.com/docs/guides/auth/auth-email-passwordless#with-otp) · [Custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
3. 从 `.env.example` 建立本机 `.env`。将 Supabase 项目 URL 填入 `MAD_SUPABASE_URL`，publishable key 填入 `MAD_SUPABASE_PUBLISHABLE_KEY`。在本机运行下列命令生成服务端会话密钥，将输出填入 `MAD_SESSION_KEY`。[Supabase API keys](https://supabase.com/docs/guides/api/api-keys)

   ```sh
   uv run --locked python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. 配置独立 `MAD_DATABASE_URL`，按 [运行基座](../../README.md#独立运行基座) 迁移并启动。HTTPS 验收使用提供受信任证书的反向代理或测试部署，将 `MAD_ORIGIN` 设置为浏览器实际访问的 HTTPS 源，部署端用同源路径提供前端和 API。
5. 两个独立浏览器会话中分别使用邮箱 A 和 B 登录。A 创建项目及邀请链接，B 登录后打开链接并加入。B 刷新页面，确认会话恢复。为验收测试项目配置短 JWT 有效期，跨过有效期后复查自动续期。A 移除 B，B 使用原项目链接再次打开，确认访问被拒。退出后核对浏览器 Cookie 与工作台回执。记录收信时间、环境、角色和结果。

本地自动验收使用 PostgreSQL 与 Supabase 邮件和身份协议替身。真实收信和受信任 HTTPS 下的续期按上述流程独立验收。
