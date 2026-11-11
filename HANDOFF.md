# Deal Radar：交给团队电脑上的 Claude

## 接手目标与当前状态

用户授权：完成网页设计、测试、虚构数据预览、可核验的联系方式 CSV，并开 PR 到 `development`。**数据库导入、真实快照生成和 Render 更新留给团队电脑操作，本次没有执行。**

- 仓库：`Ross-Business-Tech/Bloomfield-Project-1`；工作分支：`claude/keen-hamilton-q0d2fv`；PR 目标：`development`，不要推到 `main`。
- 用户最新布局要求优先：导航顺序为 **Dashboard → Overview → How it works → Explore**。Dashboard 展示地图和楼宇清单；Overview 把关键数字和原 Funnel 合为一页。旧 `#/funnel` 链接进入 Overview。
- 四页共用最大 1920px 的页面框架、边距和标题位置。所有文字统一为正文的 Inter / 等线风格无衬线字体；桌面标题 30px、小标题 20px、正文 14px、表格 13px、表头 12px。桌面 1280 / 1440 / 1920px 下清单全部列可见；手机保留表格内部滚动。
- Overview 只在顶部展示一组 List A/B/C，可点击数字区域进入清单；删除了右侧介绍和双按钮、底部重复卡片。每次进入 Overview 都重播标题、数字和依次出现的漏斗动画，离开页面取消未完成动画。条形从上至下由浅蓝渐变至海军蓝，强调最后的 Shown on Deal Radar；减少动态效果偏好会直接展示最终值。
- [PR #12](https://github.com/Ross-Business-Tech/Bloomfield-Project-1/pull/12) 当前仅供预览审核。用户明确要求不合并，请勿自动合并或部署。
- 保留联系方式面板、来源与日期、电话列、List B 无 Buyer 列，以及 A/B 共同列宽一致。新增键盘页签切换、详情焦点限制和关闭后焦点恢复。
- 已安装并完整使用 Anthropic 官方 `frontend-design` skill 复审。安装位置属于本机，不在仓库内。
- 预览命令只生成虚构楼宇、555-01xx 电话和 example.org 链接；它不读取真实联系人 CSV 或数据库。GitHub 推送/拉取不会导入真实数据。Render 使用已有真实快照时仍可显示原有线索；新增联系方式必须导入数据库并重新生成、上传快照才会显示。

## 先阅读这些文件

1. `CLAUDE.md`：仓库工作规则。
2. `docs/CONTACT_RESEARCH.md`：覆盖率、匹配规则、来源版本、限制。
3. `config/contacts_manual.csv`：**256 条可做格式检查的导入记录，覆盖 240 栋楼（240 电话 + 16 官网）**。
4. `docs/contact_sources.csv`：逐条来源、CMS 名称、城市/州、核验日期和特殊情况。
5. `docs/contacts_needs_review.csv`：3 栋楼的 4 条候选记录，**不能直接导入**。
6. `docs/DEPLOY_RENDER.md` 和 `supabase/migrations/20261111000300_facility_contact.sql`。

真实网站的 A/B 共 470 栋楼已与两份 CMS 官方名录比较，但不是所有楼的官网都已逐栋搜完。**230 栋尚无本批可导入电话，224 栋已匹配楼宇尚无本批官网**。不要把未完成核验说成“没有联系方式”，也不要补猜测值。继续查找时先用 CMS 完整 Provider Information CSV，再查机构官网。

## 在团队电脑导入

使用已经配置数据库连接的项目环境。不要将连接字符串、`.env` 内容或密钥贴到聊天、日志、提交和 PR。此前这台开发电脑没有数据库配置，不需要再向用户索要密钥。

1. 拉取工作分支，或在 PR 合并后拉取 `development`。保留团队电脑的未提交工作。确认 migration 的前置表和视图已经存在；检查迁移记录，**不要重复执行已经应用的建表语句**。
2. 按团队既有迁移流程应用 `20261111000300_facility_contact.sql`。表是 `core.facility_contact`，视图是 `app.v_facility_contact`；保留 RLS，不给 anon/authenticated 放权限。
3. 先做无需数据库的格式检查：

   ```sh
   python -m src.enrich.contacts manual --csv config/contacts_manual.csv --dry-run
   ```

   本次结果为 `256 usable rows, 0 skipped`。**这个 dry-run 不检查数据库匹配**，且当前 CLI 即使跳过坏行也可能返回成功退出码，要阅读输出。

4. 在写入前，把 CSV 的 240 个唯一 CCN 与 `core.facility` 核对。只查询必要字段：`facility_id, ccn, canonical_name, street, city, state, zip5`，并与来源台账比对。只按已有 CCN 或已经确认的 UUID 导入；不要从名字猜 UUID，也不要把 NPI、州许可证号、CMS PDF 的 FACID 当成项目的 facility_id。报告缺失 CCN，并保留未匹配项。
5. 核对来源是否仍然有效，再正式导入：

   ```sh
   python -m src.enrich.contacts manual --csv config/contacts_manual.csv
   ```

   理想结果为 `written: 256, building_not_found: 0`，但只有真实数据库核验后才能确认。`written` 是处理成功的记录数，包含更新，不等于新增行数。该导入可以重跑；同一楼、类型和值不会重复插入。它不会删除旧值；如果已有互相冲突的电话，需要单独复核，不能盲删。

6. 扩大电话覆盖：从 [CMS Provider Information](https://data.cms.gov/provider-data/dataset/4pq5-n9py) 下载当前 CSV，存入 `output/`，先执行下面第一条，再检查结果并决定导入。下载在本次环境返回 Access Denied，所以本批电话来自官方 PDF，未使用失败下载的数据。

   ```sh
   python -m src.enrich.contacts cms --csv output/NH_ProviderInfo.csv --dry-run
   python -m src.enrich.contacts cms --csv output/NH_ProviderInfo.csv
   ```

   这是按 CCN 导入数据库中匹配楼宇的电话，不限 A/B。先比较已有值；如 CMS 最新值与已核验官网冲突，先查清楚。当前视图按核验日期和创建日期选值，同日重复导入不保证后导入的来源一定优先。

7. 候选记录：Fountains of Hope 官网明确改名为 The Jasper of Sarasota；Toney Rest Home 需要内部 UUID 和更新的来源；AdviniaCare Orchard 的 CMS 城市为 East Providence，而旧网页名单写 Riverside。先核对具体街道地址、州、CCN/许可证，再将确定记录移入正式 CSV。不要直接运行候选文件。

只有楼宇总机和公开官网可以入库。来源里即使出现个人姓名、邮箱、手机号，也不要采集。未知字段保持 NOT FOUND。官网与电话来源可不同；台账中的 `checked_at_utc` 是本次查阅日期，导入程序的 `verified_at` 会写入导入当日，请保留两者差别。

## 生成快照并更新既有 Render 服务

1. PR 审核合并到 `development` 后，确认团队电脑代码与该分支一致。不要直接部署本地未审核工作分支。
2. 在团队环境运行：

   ```sh
   python -m src.web.snapshot
   python -m pytest -q tests/web
   ```

   输出 `output/site_snapshot.json` 和 `output/site_snapshot.b64`。检查联系人覆盖、来源链接、NOT FOUND、公开字段白名单，以及体积检查。不要提交或公开上传这些文件。
3. 在既有 Render 服务 `bloomfield-project-1.onrender.com` 中确认部署分支为 `development`。按 `docs/DEPLOY_RENDER.md`，替换 Secret File `site_snapshot.b64` 的内容。网站运行时只读这个文件，**不需要数据库 URL 或 Supabase 密钥**。
4. 保留既有 `SITE_PASSWORD`，确认 `SNAPSHOT_PATH=/etc/secrets/site_snapshot.b64`。先保留旧快照作为团队电脑上的私有回退文件，再手动部署。
5. 验收：无密码时页面/API/CSV 不可读；登录后 Dashboard 和 Overview 切换顶栏不抖动；Overview 有关键数字与漏斗；点击 A/B 入口进入对应清单；选楼可看到电话、官网、来源；下载 CSV 含联系方式；缺失值为 NOT FOUND。检查 `/healthz` 和页面快照日期。

Render 不会因为本次预览发布就更新。这里不创建新的 Render 服务，不变更既有密码，不公开真实快照。

## 复现设计预览与检查

本次完成的检查：`npm run build`、指定 Deal Radar 目录的 `npm run smoke`、`npm run smoke:radar` 均通过；`python -m pytest -q tests` 为 **269 passed, 2 skipped**；联系方式 dry-run 为 **256 usable rows, 0 skipped**。桌面和手机截图已人工查看。

项目根目录同时有旧 Vite 页面和 Python Deal Radar 页面。`npm run build` 编译前者；要验证这次修改，必须另外构建/检查 Deal Radar：

```sh
npm ci
npx playwright install chromium
npm run build
npm run build:radar
npm run smoke:radar
python -m pytest -q tests
npm run preview:radar
```

`smoke:radar` 调用未修改的 Ross IT smoke 脚本，检查五条路线的桌面/手机布局，再检查导航、A/B 列宽、筛选、键盘详情、焦点恢复和减少动态效果。截图写在 `smoke-results/`。`preview:radar` 还会调用规定的 `npm run publish:preview`；不要使用其他发布工具。发布只包含虚构数据。

如需直接运行用户要求的 `npm run smoke`，先 `npm run build:radar`，把 `HERENOW_DIST_DIR` 设为 `preview-dist`、`SMOKE_PATHS` 设为 `/,/#/overview,/#/how,/#/explore,/#/leads/B`，然后运行。不设目录时会检查旧 Vite 页面。

Windows 可以把 `PYTHON` 设为项目虚拟环境的 `python.exe`，并设置 `PYTHONUTF8=1`；Unix 可以指定 `.venv/bin/python`。不要提交本机工具路径。`build:radar`、`smoke:radar`、`preview:radar` 均支持 Windows 和 Unix。

当前临时预览：[Dashboard](https://inner-prana-3nn7.here.now/) · [Overview](https://inner-prana-3nn7.here.now/#/overview)。匿名预览初次发布后约 24 小时到期；如失效，运行上面的发布命令，使用命令新打印的链接。认领链接只发给用户，不放进公开仓库。

## 不能改动或提交

- 不改 `scripts/`、`.github/`、`.claude/`、`admin/`、`CLAUDE.md`。
- 不提交密钥、`.env`、真实快照、原始名单、来源下载、浏览器登录信息或个人信息；`output/`、`preview-dist/`、`smoke-results/` 保持忽略。
- 测试只用虚构机构、555-01xx 电话、example.org。真实联系方式 CSV 是明确带来源的业务数据，不作为测试 fixture。
- 不推到 `main`。后续变更继续用面向用户的 PR 标题、逐项说明和预览链接，目标 `development`。
