import asyncio
import time
from datetime import datetime
from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from ..utils import save_json, extract_target_id_from_message, resolve_member_name

# 全局内存缓存
propose_requests = {}

async def cmd_propose(plugin_instance, event: AstrMessageEvent):
    """发起求婚指令的逻辑"""
    if event.is_private_chat():
        yield event.plain_result("求婚只能在群聊中进行哦~")
        return

    user_id = str(event.get_sender_id())
    group_id = str(event.get_group_id())
    target_id = extract_target_id_from_message(event)

    if not target_id or target_id == "all":
        yield event.plain_result("请 @ 一个你想求婚的人。")
        return
    if target_id == user_id:
        yield event.plain_result("不能向自己求婚哦！")
        return

    # --- 1. 24小时求婚保护检查 ---
    now = time.time()
    protection_seconds = 24 * 3600 
    
    user_last_marriage = plugin_instance.forced_records.get(group_id, {}).get(user_id, 0)
    target_last_marriage = plugin_instance.forced_records.get(group_id, {}).get(target_id, 0)

    if now - user_last_marriage < protection_seconds:
        yield event.plain_result("你还在新婚保护期内（24小时），暂时不能向别人求婚。")
        return
    if now - target_last_marriage < protection_seconds:
        yield event.plain_result("对方还在新婚保护期内（24小时），先不要打扰人家啦~")
        return

    # --- 2. 解析被求婚者的名称 ---
    target_name = f"用户({target_id})"
    try:
        if event.get_platform_name() == "aiocqhttp" and isinstance(event, AiocqhttpMessageEvent):
            members = await event.bot.api.call_action(
                "get_group_member_list", group_id=int(group_id)
            )
            if isinstance(members, dict) and "data" in members:
                members = members["data"]
            target_name = resolve_member_name(members, user_id=target_id, fallback=target_name)
    except Exception:
        pass

    if group_id not in propose_requests:
        propose_requests[group_id] = {}
    
    propose_requests[group_id][target_id] = {
        "proposer_id": user_id,
        "proposer_name": event.get_sender_name() or f"用户({user_id})",
        "target_name": target_name,
        "expire": now + 30,
        "umo": event.unified_msg_origin
    }

    yield event.plain_result(f"🌹 @{event.get_sender_name()} 向 【{target_name}】 发起了求婚！\n请在 30 秒内回复“同意”来接受。")

    # 3. 异步等待
    await asyncio.sleep(30)
    
    if group_id in propose_requests and target_id in propose_requests[group_id]:
        req = propose_requests[group_id][target_id]
        if req["proposer_id"] == user_id:
            # --- 彻底修复 ValidationError ---
            # 创建空的消息链
            chain_obj = MessageChain()
            
            # 手动构建列表，确保不触发 MessageChain().message() 的字符串校验
            components = [
                Comp.At(qq=user_id),
                Comp.Plain(text=" ...很遗憾，求婚超时了，对方似乎没有答应...")
            ]
            
            # 直接赋值给 chain 属性，这是最稳健的绕过 Pydantic 校验的方法
            chain_obj.chain = components
            
            try:
                # 调用插件上下文发送主动消息
                await plugin_instance.context.send_message(req["umo"], chain_obj)
            except Exception as e:
                from astrbot.api import logger
                logger.error(f"[propose] 发送超时提醒失败: {e}")
            
            del propose_requests[group_id][target_id]

async def handle_propose_response(plugin_instance, event: AstrMessageEvent):
    """处理同意回复逻辑"""
    group_id = str(event.get_group_id())
    user_id = str(event.get_sender_id())
    msg = event.message_str.strip()

    if group_id in propose_requests and user_id in propose_requests[group_id]:
        req = propose_requests[group_id][user_id]
        
        if time.time() > req["expire"]:
            del propose_requests[group_id][user_id]
            return

        if msg in ["同意求婚", "我同意", "同意"]:
            proposer_id = req["proposer_id"]
            proposer_name = req["proposer_name"]
            target_name = req["target_name"]
            
            timestamp = datetime.now().isoformat()
            group_records = plugin_instance._get_group_records(group_id)
            
            group_records[:] = [r for r in group_records if r["user_id"] not in [user_id, proposer_id]]
            
            marriage_data = [
                {"user_id": proposer_id, "wife_id": user_id, "wife_name": target_name, "timestamp": timestamp, "forced": True},
                {"user_id": user_id, "wife_id": proposer_id, "wife_name": proposer_name, "timestamp": timestamp, "forced": True}
            ]
            group_records.extend(marriage_data)

            now = time.time()
            cooldown_ts = now + 129600 # 36小时
            
            if group_id not in plugin_instance.forced_records:
                plugin_instance.forced_records[group_id] = {}
            
            plugin_instance.forced_records[group_id][user_id] = cooldown_ts
            plugin_instance.forced_records[group_id][proposer_id] = cooldown_ts

            save_json(plugin_instance.records_file, plugin_instance.records)
            save_json(plugin_instance.forced_file, plugin_instance.forced_records)
            
            del propose_requests[group_id][user_id]
            
            event.stop_event()
            yield event.plain_result(f"🎉 恭喜！{target_name} 接受了 {proposer_name} 的求婚！\n你们已正式结为夫妻，36小时内无法强娶他人，24小时内也不会受到他人求婚扰乱。")