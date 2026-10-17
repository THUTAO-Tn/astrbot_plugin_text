from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from datetime import date

class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)


    @filter.command("签到") # 注册名为签到的指令 发送/签到后执行下面代码
    async def qiandao(self,event:AstrMessageEvent):
        user_name = event.get_sender_name() # 获取发送消息者的名字
        user_id = event.get_sender_id() # 获取发送消息者的id
        today = str(date.today()) # 获取日期
        # 当前（上次）签到时间和签到天数
        last_sign_date = await self.get_kv_data(f"name_{user_id}","")
        total = await self.get_kv_data(f"name_{user_id}",0)
        # 签到开始⬇
        if last_sign_date != total:
            total += 1
            await self.put_kv_data(f"name_{user_id}",today)
            await self.put_kv_data(f"name_{user_id}",total)
            yield event.plain_result(f"签到成功{user_name}\n今天是你签到的第{total}天")
        else:
            yield event.plain_result(f"今天已经签到了~")


    @filter.command("help")
    async def help(self,event:AstrMessageEvent):
        yield event.plain_result("显示本帮助")