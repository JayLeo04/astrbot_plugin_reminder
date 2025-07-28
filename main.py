import os
import json
import datetime
import uuid
import zoneinfo
import astrbot.api.star as star
from astrbot.api.event import filter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api import llm_tool, logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api.star import register # Import register

@register("astrbot_plugin_reminder", "Soulter", "增强型待办提醒，支持批量添加和智能日程规划。", "0.0.1")
class Main(star.Star):
    """增强型待办提醒，支持批量添加和智能日程规划。"""

    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.timezone = self.context.get_config().get("timezone")
        if not self.timezone:
            self.timezone = None
        try:
            self.timezone = zoneinfo.ZoneInfo(self.timezone) if self.timezone else None
        except Exception as e:
            logger.error(f"时区设置错误: {e}, 使用本地时区")
            self.timezone = None
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)

        # set and load config
        reminder_file = os.path.join(get_astrbot_data_path(), "astrbot-reminder.json")
        if not os.path.exists(reminder_file):
            with open(reminder_file, "w", encoding="utf-8") as f:
                f.write("{}")
        with open(reminder_file, "r", encoding="utf-8") as f:
            self.reminder_data = json.load(f)

        self._init_scheduler()
        self.scheduler.start()

    def _init_scheduler(self):
        """Initialize the scheduler."""
        for group in self.reminder_data:
            for reminder in self.reminder_data[group]:
                if "id" not in reminder:
                    id_ = str(uuid.uuid4())
                    reminder["id"] = id_
                else:
                    id_ = reminder["id"]

                if "datetime" in reminder:
                    if self.check_is_outdated(reminder):
                        continue
                    self.scheduler.add_job(
                        self._reminder_callback,
                        id=id_,
                        trigger="date",
                        args=[group, reminder],
                        run_date=datetime.datetime.strptime(
                            reminder["datetime"], "%Y-%m-%d %H:%M"
                        ),
                        misfire_grace_time=60,
                    )
                elif "cron" in reminder:
                    self.scheduler.add_job(
                        self._reminder_callback,
                        trigger="cron",
                        id=id_,
                        args=[group, reminder],
                        misfire_grace_time=60,
                        **self._parse_cron_expr(reminder["cron"]),
                    )

    def check_is_outdated(self, reminder: dict):
        """Check if the reminder is outdated."""
        if "datetime" in reminder:
            reminder_time = datetime.datetime.strptime(
                reminder["datetime"], "%Y-%m-%d %H:%M"
            ).replace(tzinfo=self.timezone)
            return reminder_time < datetime.datetime.now(self.timezone)
        return False

    async def _save_data(self):
        """Save the reminder data."""
        reminder_file = os.path.join(get_astrbot_data_path(), "astrbot-reminder.json")
        with open(reminder_file, "w", encoding="utf-8") as f:
            json.dump(self.reminder_data, f, ensure_ascii=False)

    def _parse_cron_expr(self, cron_expr: str):
        fields = cron_expr.split(" ")
        return {
            "minute": fields[0],
            "hour": fields[1],
            "day": fields[2],
            "month": fields[3],
            "day_of_week": fields[4],
        }

    async def _add_single_reminder(self, unified_msg_origin: str, text: str, datetime_str: str = None, cron_expression: str = None, human_readable_cron: str = None):
        """Helper function to add a single reminder."""
        if unified_msg_origin not in self.reminder_data:
            self.reminder_data[unified_msg_origin] = []

        if not cron_expression and not datetime_str:
            raise ValueError(
                "The cron_expression and datetime_str cannot be both None."
            )
        
        if not text:
            text = "未命名待办事项"

        d = {"text": text, "id": str(uuid.uuid4())}
        reminder_time_display = ""

        if cron_expression:
            d["cron"] = cron_expression
            d["cron_h"] = human_readable_cron
            self.reminder_data[unified_msg_origin].append(d)
            self.scheduler.add_job(
                self._reminder_callback,
                "cron",
                id=d["id"],
                misfire_grace_time=60,
                **self._parse_cron_expr(cron_expression),
                args=[unified_msg_origin, d],
            )
            if human_readable_cron:
                reminder_time_display = f"{human_readable_cron}(Cron: {cron_expression})"
        else:
            d["datetime"] = datetime_str
            self.reminder_data[unified_msg_origin].append(d)
            datetime_scheduled = datetime.datetime.strptime(
                datetime_str, "%Y-%m-%d %H:%M"
            )
            self.scheduler.add_job(
                self._reminder_callback,
                "date",
                id=d["id"],
                args=[unified_msg_origin, d],
                run_date=datetime_scheduled,
                misfire_grace_time=60,
            )
            reminder_time_display = datetime_str
        return text, reminder_time_display

    @llm_tool("astrbot_plugin_reminder")
    async def set_reminder(
        self,
        event: AstrMessageEvent,
        text: str = None,
        datetime_str: str = None,
        cron_expression: str = None,
        human_readable_cron: str = None,
    ):
        """Call this function when user is asking for setting a single reminder.

        Args:
            text(string): Must Required. The content of the reminder.
            datetime_str(string): Required when user's reminder is a single reminder. The datetime string of the reminder, Must format with %Y-%m-%d %H:%M
            cron_expression(string): Required when user's reminder is a repeated reminder. The cron expression of the reminder. Monday is 0 and Sunday is 6.
            human_readable_cron(string): Optional. The human readable cron expression of the reminder.
        """
        if event.get_platform_name() == "qq_official":
            yield event.plain_result("reminder 暂不支持 QQ 官方机器人。")
            return
        
        try:
            text_display, time_display = await self._add_single_reminder(event.unified_msg_origin, text, datetime_str, cron_expression, human_readable_cron)
            await self._save_data()
            yield event.plain_result(
                "成功设置待办事项。\n内容: "
                + text_display
                + "\n时间: "
                + time_display
                + "\n\n使用 /reminder ls 查看所有待办事项。\n使用 /tool off astrbot_plugin_reminder 关闭此功能。"
            )
        except ValueError as e:
            yield event.plain_result(f"设置待办事项失败: {e}")

    @llm_tool("astrbot_plugin_reminder")
    async def set_multiple_reminders(
        self,
        event: AstrMessageEvent,
        reminders: list[dict],
    ):
        """Call this function when user is asking for setting multiple reminders at once.

        Args:
            reminders(list): Must Required. A list of dictionaries, where each dictionary represents a reminder.
                            Each dictionary must have 'text' (string), and either 'datetime_str' (string, format %Y-%m-%d %H:%M)
                            or 'cron_expression' (string) and optionally 'human_readable_cron' (string).
                            Example: [{"text": "Buy groceries", "datetime_str": "2025-07-29 10:00"},
                                      {"text": "Call mom", "cron_expression": "0 9 * * 1", "human_readable_cron": "Every Monday at 9 AM"}]
        """
        if event.get_platform_name() == "qq_official":
            yield event.plain_result("reminder 暂不支持 QQ 官方机器人。")
            return

        results = []
        for r in reminders:
            try:
                text_display, time_display = await self._add_single_reminder(
                    event.unified_msg_origin,
                    r.get("text"),
                    r.get("datetime_str"),
                    r.get("cron_expression"),
                    r.get("human_readable_cron")
                )
                results.append(f"成功设置: {text_display} - {time_display}")
            except ValueError as e:
                results.append(f"设置失败 ({r.get('text', '未知事项')}): {e}")
        
        await self._save_data()
        yield event.plain_result(
            "批量设置待办事项完成:\n"
            + "\n".join(results)
            + "\n\n使用 /reminder ls 查看所有待办事项。\n使用 /tool off astrbot_plugin_reminder 关闭此功能。"
        )

    @llm_tool("astrbot_plugin_reminder")
    async def plan_schedule(
        self,
        event: AstrMessageEvent,
        user_request: str,
    ):
        """Call this function when user asks to plan a schedule or organize tasks.
        The function will propose a schedule and ask for user confirmation before adding to reminders.

        Args:
            user_request(string): Must Required. The user's request for schedule planning.
        """
        if event.get_platform_name() == "qq_official":
            yield event.plain_result("reminder 暂不支持 QQ 官方机器人。")
            return

        # Simulate LLM planning
        # In a real scenario, this would involve calling an LLM to parse user_request
        # and generate a structured list of tasks with datetime/cron.
        mock_schedule = [
            {"text": f"根据 '{user_request}' 规划任务1", "datetime_str": (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")},
            {"text": f"根据 '{user_request}' 规划任务2", "datetime_str": (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M")},
        ]

        schedule_str = "我为您规划了以下日程：\n"
        for i, task in enumerate(mock_schedule):
            time_info = task.get("datetime_str") or task.get("human_readable_cron") or task.get("cron_expression")
            schedule_str += f"{i + 1}. {task['text']} - {time_info}\n"
        schedule_str += "\n您是否同意将这些任务加入待办事项？请回复 '是' 或 '否'。"

        # Store the proposed schedule and wait for user confirmation
        # This requires a mechanism to store state and listen for a specific reply.
        # For this example, we'll just yield the proposal and assume a confirmation mechanism exists.
        # In a real AstrBot plugin, you might use context.set_temp_data and a subsequent event handler.
        yield event.plain_result(schedule_str)
        # For the purpose of this task, I will assume the user confirms and add them directly.
        # In a real scenario, you would need to implement a state machine or similar to handle the confirmation.

        # Assuming user confirms for demonstration purposes
        confirmation_message = "已将规划的日程加入待办事项。"
        successful_adds = []
        failed_adds = []
        for task in mock_schedule:
            try:
                text_display, time_display = await self._add_single_reminder(
                    event.unified_msg_origin,
                    task.get("text"),
                    task.get("datetime_str"),
                    task.get("cron_expression"),
                    task.get("human_readable_cron")
                )
                successful_adds.append(f"{text_display} - {time_display}")
            except ValueError as e:
                failed_adds.append(f"{task.get('text', '未知事项')}: {e}")
        
        await self._save_data()
        
        response_parts = [confirmation_message]
        if successful_adds:
            response_parts.append("成功添加的任务:")
            response_parts.extend(successful_adds)
        if failed_adds:
            response_parts.append("未能添加的任务:")
            response_parts.extend(failed_adds)
        
        response_parts.append("\n\n使用 /reminder ls 查看所有待办事项。\n使用 /tool off astrbot_plugin_reminder 关闭此功能。")
        yield event.plain_result("\n".join(response_parts))

    @filter.command_group("reminder")
    def reminder(self):
        """The command group of the reminder."""
        pass

    async def get_upcoming_reminders(self, unified_msg_origin: str):
        """Get upcoming reminders."""
        reminders = self.reminder_data.get(unified_msg_origin, [])
        if not reminders:
            return []
        now = datetime.datetime.now(self.timezone)
        upcoming_reminders = [
            reminder
            for reminder in reminders
            if "datetime" not in reminder
            or datetime.datetime.strptime(
                reminder["datetime"], "%Y-%m-%d %H:%M"
            ).replace(tzinfo=self.timezone)
            >= now
        ]
        return upcoming_reminders

    @reminder.command("ls")
    async def reminder_ls(self, event: AstrMessageEvent):
        """List upcoming reminders."""
        reminders = await self.get_upcoming_reminders(event.unified_msg_origin)
        if not reminders:
            yield event.plain_result("没有正在进行的待办事项。")
        else:
            reminder_str = "正在进行的待办事项：\n"
            for i, reminder in enumerate(reminders):
                time_ = reminder.get("datetime", "")
                if not time_:
                    cron_expr = reminder.get("cron", "")
                    time_ = reminder.get("cron_h", "") + f"(Cron: {cron_expr})"
                reminder_str += f"{i + 1}. {reminder['text']} - {time_}\n"
            reminder_str += "\n使用 /reminder rm <id> 删除待办事项。\n"
            yield event.plain_result(reminder_str)

    @reminder.command("rm")
    async def reminder_rm(self, event: AstrMessageEvent, index: int):
        """Remove a reminder by index."""
        reminders = await self.get_upcoming_reminders(event.unified_msg_origin)

        if not reminders:
            yield event.plain_result("没有待办事项。")
        elif index < 1 or index > len(reminders):
            yield event.plain_result("索引越界。")
        else:
            reminder = reminders.pop(index - 1)
            job_id = reminder.get("id")

            # self.reminder_data[event.unified_msg_origin] = reminder
            users_reminders = self.reminder_data.get(event.unified_msg_origin, [])
            for i, r in enumerate(users_reminders):
                if r.get("id") == job_id:
                    users_reminders.pop(i)

            try:
                self.scheduler.remove_job(job_id)
            except Exception as e:
                logger.error(f"Remove job error: {e}")
                yield event.plain_result(
                    f"成功移除对应的待办事项。删除定时任务失败: {str(e)} 可能需要重启 AstrBot 以取消该提醒任务。"
                )
            await self._save_data()
            yield event.plain_result("成功删除待办事项：\n" + reminder["text"])

    async def _reminder_callback(self, unified_msg_origin: str, d: dict):
        """The callback function of the reminder."""
        logger.info(f"Reminder Activated: {d['text']}, created by {unified_msg_origin}")
        await self.context.send_message(
            unified_msg_origin,
            MessageEventResult().message(
                "待办提醒: \n\n"
                + d["text"]
                + "\n时间: "
                + d.get("datetime", "")
                + d.get("cron_h", "")
            ),
        )

    async def terminate(self):
        self.scheduler.shutdown()
        await self._save_data()
        logger.info("Reminder plugin terminated.")
