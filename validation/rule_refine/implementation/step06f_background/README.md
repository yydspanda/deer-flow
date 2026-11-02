# 第6步F：接入现有LOCAL后台，默认关闭

6E已确认内网源码摘要与参考包相同，可见进程布局与local_run.sh相符。本步沿用tasks.task.app、periodic_queue单并发worker和beat，安装后保持自动分析关闭。DeerFlow生产代码不受影响。

## 本次执行

完整粘贴APPLY-STEP06F.sh。脚本内置zlib/base64安装内容，不下载文件；解码前校验整包SHA，写入前核对前序文件及目标版本，编译所有Python文件。install-manifest.json保存可读原内容，payload保存各模块/测试源码。

新增background_tick.py/background_service.py及两个测试；只在原tasks/task.py与config/celery_config.py末尾追加注册及定时项，两份原件保留为.before-step06f。保留.gitignore已有内容，仅增加独立配置的忽略项。同版本重复安装不重复追加；版本不符停止。已有分析配置开启时停止本次安装，避免安装期间进入真实调用。

新增config/rule_refine.analysis.local.json，enabled=false、model=DeepSeek_V32、state_dir=~/sec-rule-refine-data/background。不修改原监测配置、不重新启动服务、不把监测开启等同于分析开启。

脚本顺序运行21项轮转测试、12项后台适配测试、轮转演示、实际安装Celery库的独立注册检查。回传两组测试汇总和最后两份JSON。Celery检查使用独立memory://应用及替换的关闭回调，不导入原tasks.task、不连接真实broker。此处不证明原worker已加载新任务；启用与重启在回传通过后另给命令。

## 运行约定

- LOCAL beat增加rule_refine_pending，每30秒向periodic_queue派发tasks.task.rule_refine_tick，排队过期25秒，忽略任务结果，Celery不做隐式自动重试。过期不取消已经执行的任务。
- 每次后台调用自行读取分析文件及监测配置到环境副本，检查env_profile、ENV_PROFILE以及已缓存rc.ENV均为LOCAL。默认关闭路径在加载应用连接依赖前返回。应用数据库必须为当前项目nas/data/sec_know_model.sqlite，模型代理限本机回环地址。
- 规则配置仍由CsvRuleConfigSource提供；任务读取仍用6C。每次交付重新读配置。结果与模拟接收保存在state_dir下的命名空间摘要目录，包含scheduler/processing/receipts。
- 用原RedisModelSemaphore及get_model_policy取得模型共享并发槽，保留原槽命名空间；入口、调用前后核对租约，沿用现有续租。模型槽忙或熔断返回capacity_wait，在写analysis_started之前结束，不消耗后台重试次数。当前交付已有结果也经过此槽，属于保守实现。
- 模型调用沿用4C：SDK重试0、超时240秒、trust_env=false；这不是整个后台任务的强制墙钟截止，代理内部尝试次数仍未知。熔断只按传输失败记录，不将规则建议耗时混入原完整Workflow耗时估计。
- 单次tick基于当前目录构造最多5000个规范规则作用域，通过一批最多256个GET查找待处理信号，不扫描全Redis。持久游标按作用域摘要轮转；每次最多处理一个。911个作用域、无分析耗时情况下约四次tick可扫描一圈；真实延迟还受共享worker和模型调用影响。
- 查询到的信号快照固定后交给6D，确认仍按原signal_id条件删除，不能误删后来信号。每条开始处理前先保存游标、尝试次数和下次时间；本机flock防止同目录重叠执行。
- 重试间隔默认60秒，最多3次自动尝试。blocked、analysis_unconfirmed、无效信号或存档验证失败立即标needs_attention，保留原信号及证据。失败不阻塞其他规则轮转；新signal_id重置该规则重试次数。状态计数仅覆盖本次查询批次，不是全库统计。
- reset_background_retry(directory)是显式解除后台重试暂停的函数，保留游标、分析、交付及Redis信号。它不重新生成已经固化的analysis_failed/analysis_unconfirmed结果；相应处理需要单独核对。安装过程不调用重置。

## 边界

当前只有LOCAL单机SQLite/文件锁，不能直接用于多机部署。只枚举当前CSV目录，被删除或改变归属的规则可能留下旧pending，需要后续单独处理。运营报告、真实告警验收、正式ZEUS接口仍未完成；LocalRecommendationSink始终mock/applied=false，不会修改线上规则。

## 本地验证

21项轮转、12项适配测试通过；覆盖并发锁、游标、公平轮换、跨重启冷却、三次上限、暂停/重置、信号替换、真实计数器键与时间格式兼容、默认关闭、配置隔离、模型槽获取时机、工厂依赖和资源关闭。Python3.11语法及Ruff/Bash检查通过。

首次与重复安装演练通过，原文件副本摘要正确，忽略项只出现一次、原忽略内容保留，默认配置权限0600；依赖/待更新文件/新增文件冲突以及已开启配置均在修改其他代码前停止。LOCAL定时项队列/过期验证通过，非LOCAL不添加。

本地环境未安装Celery，本地注册测试使用人工app。内网已回传12项适配测试通过（0.019秒）、轮转演示通过，以及Celery5.4.0独立注册检查通过；本次未另贴21项轮转测试汇总。真实broker、worker重启与自动模型链路尚未验证，下一步见../step06g_scheduler_check/。
