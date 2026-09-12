"""
礼物感谢插件 - 自动感谢观众送出的礼物

支持变量替换:
    {用户名}   用户昵称
    {用户ID}   用户ID
    {礼物名称} 礼物名称
    {礼物数量} 礼物数量（合并后的总数）
    {礼物单价} 礼物单价
    {总价值}   总花费（金瓜子，合并后的总价值）
    {是否盲盒} 是否盲盒
    {盲盒名称} 盲盒开出的礼物名
    {盲盒花费} 盲盒花费（金瓜子）= 总价值
    {盲盒价值} 盲盒开出价值（金瓜子）= 礼物单价 × 礼物数量
    {盲盒盈亏} 盲盒盈亏（金瓜子）= 盲盒价值 - 盲盒花费
    {粉丝牌名称} 粉丝牌名称
    {粉丝牌等级} 粉丝牌等级
    {舰长等级} 舰长等级

消息合并机制：
    同一用户的相同礼物在合并窗口（默认3秒）内会合并数量，
    超过窗口没有新的相同礼物到达时，才发送感谢回复。
"""

import random
import re
import threading
from core.plugin_manager import BasePlugin


class GiftThanksPlugin(BasePlugin):
    """礼物感谢插件"""

    def __init__(self, name: str, plugin_path: str):
        super().__init__(name, plugin_path)
        # 合并窗口内的待发送消息: {merge_key: merged_message_dict}
        self._pending = {}
        # 每个合并项对应的定时器: {merge_key: Timer}
        self._timers = {}
        self._lock = threading.Lock()

    def process_message(self, message: dict):
        if message.get('消息类型') != '礼物':
            return

        # 忽略自己发的消息
        if self.is_self_danmu(message):
            return

        # 构建合并键: (用户ID, 礼物名称, 是否盲盒, 盲盒名称)
        uid = str(message.get('用户ID', ''))
        gift_name = message.get('礼物名称', '')
        is_blind = message.get('是否盲盒', False)
        blind_name = message.get('盲盒名称', '')
        merge_key = (uid, gift_name, is_blind, blind_name)

        merge_window = self._config.get('合并窗口秒', 3)

        print(f"[GiftThanks] 收到礼物: uid={uid}, 礼物={gift_name}, 是否盲盒={is_blind}, 盲盒名称={blind_name}, 数量={message.get('礼物数量')}")

        with self._lock:
            if merge_key in self._pending:
                # 窗口内收到相同礼物，合并
                self._merge_message(merge_key, message)
                print(f"[GiftThanks] 合并: {gift_name}, 当前总数={self._pending[merge_key].get('礼物数量')}")
                # 重置定时器（从最后一次收到的时间开始算）
                self._cancel_timer(merge_key)
                self._start_timer(merge_key, merge_window)
            else:
                # 新的合并项
                self._pending[merge_key] = self._copy_message(message)
                print(f"[GiftThanks] 新合并项: {gift_name}, {merge_window}秒后发送")
                self._start_timer(merge_key, merge_window)

    def _copy_message(self, message: dict) -> dict:
        """复制消息用于合并（转换数值字段为可累加的 int）"""
        msg = dict(message)
        # 确保数值字段为 int
        for key in ['礼物数量', '礼物单价', '总价值']:
            try:
                msg[key] = int(msg.get(key, 0))
            except (ValueError, TypeError):
                msg[key] = 0
        return msg

    def _merge_message(self, merge_key: tuple, new_msg: dict):
        """将新消息合并到待发送消息中"""
        pending = self._pending[merge_key]
        pending['礼物数量'] = pending.get('礼物数量', 0) + int(new_msg.get('礼物数量', 0))
        pending['总价值'] = pending.get('总价值', 0) + int(new_msg.get('总价值', 0))
        # 更新用户名、粉丝牌等最新信息
        for key in ['用户名', '头像URL', '粉丝牌名称', '粉丝牌等级', '舰长等级']:
            if new_msg.get(key):
                pending[key] = new_msg[key]

    def _start_timer(self, merge_key: tuple, delay: float):
        """启动定时器，到期后发送合并消息"""
        timer = threading.Timer(delay, self._flush, args=[merge_key])
        timer.daemon = True
        self._timers[merge_key] = timer
        timer.start()

    def _cancel_timer(self, merge_key: tuple):
        """取消定时器"""
        timer = self._timers.pop(merge_key, None)
        if timer:
            timer.cancel()

    def _flush(self, merge_key: tuple):
        """定时器到期，发送合并后的消息"""
        with self._lock:
            message = self._pending.pop(merge_key, None)
            self._timers.pop(merge_key, None)

        if not message:
            return

        print(f"[GiftThanks] 定时器触发，准备发送感谢: {message.get('礼物名称')}, 数量={message.get('礼物数量')}, 是否盲盒={message.get('是否盲盒')}")

        # 发送合并后的感谢
        self._send_thanks(message)

    def _send_thanks(self, message: dict):
        """根据消息选择模板并发送感谢"""
        is_blind = message.get('是否盲盒', False)
        print(f"[GiftThanks] _send_thanks: 是否盲盒={is_blind}, 类型={type(is_blind)}")
        
        if is_blind:
            template_list = self._config.get('盲盒感谢列表', [])
            print(f"[GiftThanks] 使用盲盒模板列表，共 {len(template_list)} 条")
        else:
            template_list = self._config.get('普通感谢列表', [])
            print(f"[GiftThanks] 使用普通模板列表，共 {len(template_list)} 条")

        if not template_list:
            print("[GiftThanks] 模板列表为空，跳过发送")
            return

        template = random.choice(template_list)
        print(f"[GiftThanks] 选中模板: {template}")
        reply = self._substitute_variables(template, message)
        print(f"[GiftThanks] 替换后: {reply}")

        if reply:
            result = self.send_danmu(reply)
            print(f"[GiftThanks] 发送结果: {result}")
            if result.get('success'):
                print(f"[GiftThanks] 已感谢: {reply}")

    def cleanup(self):
        """实例销毁时取消所有待处理的合并定时器，防止线程泄漏"""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._pending.clear()

    def _substitute_variables(self, template: str, message: dict) -> str:
        """
        将模板中的 {变量名} 替换为消息中的实际值
        
        变量名直接使用消息字典的键名，如 {用户名} {礼物名称} 等
        未匹配的变量保持原样
        
        金额变量默认CNY格式(÷1000保留两位小数):
        - 盲盒花费: 用户实际花费
        - 盲盒价值: 开出礼物价值(单价×数量)
        - 盲盒盈亏: 盲盒价值-盲盒花费(正=赚,负=亏)
        - 总价值: 普通礼物总价值
        """
        # 计算盲盒衍生变量
        gift_price = message.get('礼物单价', 0)
        gift_count = message.get('礼物数量', 0)
        total_coin = message.get('总价值', 0)
        
        try:
            gift_price = int(gift_price) if gift_price else 0
            gift_count = int(gift_count) if gift_count else 0
            total_coin = int(total_coin) if total_coin else 0
        except (ValueError, TypeError):
            gift_price = 0
            gift_count = 0
            total_coin = 0
        
        blind_value = gift_price * gift_count
        blind_cost = total_coin
        blind_profit = blind_value - blind_cost

        # 构建变量字典
        variables = {}
        for key in [
            '用户名', '用户ID', '礼物名称', '礼物数量',
            '礼物单价', '总价值', '是否盲盒',
            '盲盒名称',
            '粉丝牌名称', '粉丝牌等级', '舰长等级'
        ]:
            val = message.get(key, '')
            if val is None:
                val = ''
            variables[key] = str(val)

        # CNY格式 (÷1000 保留两位小数)
        variables['盲盒花费'] = f"{blind_cost / 1000:.2f}"
        variables['盲盒价值'] = f"{blind_value / 1000:.2f}"
        variables['盲盒盈亏'] = f"{blind_profit / 1000:+.2f}"
        variables['总价值'] = f"{total_coin / 1000:.2f}"

        # 使用安全的替换，未匹配的变量保留原样
        # 匹配 {变量名}，变量名可以是任意字符（包括中文和下划线），非贪婪匹配
        pattern = re.compile(r'\{([^{}]+)\}')

        def _replace_match(m):
            var_name = m.group(1)
            return variables.get(var_name, m.group(0))

        return pattern.sub(_replace_match, template)
