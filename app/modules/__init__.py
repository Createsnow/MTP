"""业务模块（docs/模块化单体改造方案.md 第 4、8 章）：每个子包一个模块，module.py 里声明 MODULE。

阶段 1：模块的代码仍在 app/services、app/api/routes（阶段 4 再搬进来），这里只登记各模块有哪些路由、启动步骤和后台任务。
新模块（箱号 box、托号 pallet）直接在这里新建子包：module.py、api.py、routes/、services/、errors.py，
表用 box_ / plt_ 前缀。
"""
