"""不依赖旧产品字段的响应展示。完整数据由连接层调用方保留。"""
import json


def smart_response(data, limit: int = 4000) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return json.dumps({'truncated': True, 'total_characters': len(text),
                       'preview': text[:max(0, limit)],
                       'message': '仅展示部分响应；不能据此判断数据完整。'}, ensure_ascii=False)
