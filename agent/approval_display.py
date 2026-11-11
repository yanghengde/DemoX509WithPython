"""通用确认摘要；业务展示可在此扩展。"""


def business_preview(calls, execution_details=None):
    return f'本次共 {len(calls)} 项操作。请展开执行明细核对工具及完整参数。'
