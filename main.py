from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)


    @filter.command("签到") # 注册名为签到的指令 发送/签到后执行下面代码
    async def qiandao(self,event:AstrMessageEvent):
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        a = await self.put_kv_data({user_id},0)
        if a[user_id] == 0:
            a[user_id] = 1
            yield event.plain_result(f"签到成功！{user_name}")
        else:
            yield event.plain_result(f"你今日已签到！{user_name}")




    @filter.command("help")
    async def help(self,event:AstrMessageEvent):
        yield event.plain_result("显示本帮助")