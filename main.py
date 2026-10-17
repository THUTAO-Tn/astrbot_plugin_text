from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)


    @filter.command("签到") # 注册名为签到的指令 发送/签到后执行下面代码
    async def qiandao(self,event:AstrMessageEvent):
        user_name = event.get_sender_name()
        yield event.plain_result(f"签到成功！{user_name}")



    @filter.command_help("help")
    async def help(self,event:AstrMessageEvent):
        yield event.plain_result("显示本帮助")