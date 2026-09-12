"""
抽签插件 - 弹幕触发抽签，发送结果弹幕
"""

from core.plugin_manager import BasePlugin
from datetime import datetime
import time


class LotteryPlugin(BasePlugin):
    """抽签插件"""
    
    def process_message(self, message: dict):
        config = self._config
        cooldown_store = getattr(self, '_cooldown_store', {})
        
        # 判断消息类型
        msg_type = message.get('消息类型')
        if msg_type != '弹幕':
            return
        
        # 忽略自己发出的弹幕（防止自己的回复再次触发抽签）
        if self.is_self_danmu(message):
            return

        # 判断触发词
        content = message.get('弹幕内容', '')
        trigger_word = config.get('触发词', '抽签')
        if trigger_word not in content:
            return
        
        # 获取用户ID（提前定义，供后续代码使用）
        uid = message.get('用户ID')
        
        # 粉丝牌限制
        if config.get('启用粉丝牌限制') and not message.get('粉丝牌名称', ''):
            print(f"[LotteryPlugin] 用户 {message.get('用户名')} 没有粉丝牌，跳过")
            return
        
        # 冷却检查
        if config.get('启用冷却') and uid:
            today = datetime.now().strftime('%Y-%m-%d')
            if cooldown_store.get(str(uid)) == today:
                print(f"[LotteryPlugin] 用户 {message.get('用户名')} ({uid}) 今日已抽过签")
                return
        
        # 获取签文列表
        sign_list = []
        for item in config.get('签文列表', []):
            if isinstance(item, dict) and 'title' in item and 'content' in item:
                sign_list.append({'title': item['title'], 'content': item['content']})
            else:
                s = str(item).strip()
                if s.startswith('「') and '」' in s:
                    idx = s.index('」')
                    sign_list.append({
                        'title': s[:idx+1],
                        'content': s[idx+1:].replace(',', '\n').replace('，', '\n')
                    })
                else:
                    sign_list.append({'title': s, 'content': ''})
        
        if not sign_list:
            print("[LotteryPlugin] 签文列表为空")
            return
        
        # 抽签算法
        uid_num = int(uid or 0)
        msg_timestamp = message.get('时间戳')
        
        # msg_timestamp 现在是 Unix float 时间戳
        try:
            ts_val = float(msg_timestamp) if msg_timestamp else time.time()
        except (ValueError, TypeError):
            ts_val = time.time()
        
        if config.get('全随机模式'):
            index = (int(ts_val) + uid_num) % len(sign_list)
        else:
            dt = datetime.fromtimestamp(ts_val)
            date_value = dt.year * 10000 + dt.month * 100 + dt.day
            index = (date_value + uid_num) % len(sign_list)
        
        sign = sign_list[index]
        
        user_name = message.get('用户名', '幸运用户')
        print(f"[LotteryPlugin] {user_name} 抽中: {sign['title']}")
        
        # 发送结果弹幕（使用 self.send_danmu 自动绑定当前房间）
        try:
            result_msg = f"{user_name} 抽中{sign['title']}「{sign['content'].replace('\n', '，')}」"
            self.send_danmu(result_msg)
            print(f"[LotteryPlugin] 发送弹幕成功: {result_msg}")
        except Exception as e:
            print(f"[LotteryPlugin] 发送弹幕失败: {e}")
        
        # 设置冷却
        if config.get('启用冷却') and uid:
            cooldown_store[str(uid)] = datetime.now().strftime('%Y-%m-%d')
            self._cooldown_store = cooldown_store

