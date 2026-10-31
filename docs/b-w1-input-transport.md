# B第一步：W1结构化输入传递

2026-10-31｜分支：feature/w1-w2-platform｜范围：新工作流层及离线测试。

## 修改文件与行为

- `workflows/sales.py`：校验SalesRequest，按独立JSON字段构造模型messages，经调用者传入的client生成GenerationResponse。询盘、产品资料、语言不再共用extra_info或按换行拆解；未知国家/数量保留null，不推断。空必填字段、无效数量、空模型输出返回明确错误；异常不转为正常商务回复，不输出原始服务异常内容，无自动重试。
- `providers/contracts.py`：ChatClient协议边界，约定complete(messages)返回文本或抛出结构化异常；本步骤没有实现真实API客户端或自动读取密钥。
- `tests/test_sales_transport.py`：9项离线测试，覆盖完整字段抵达模拟客户端、多行/逗号/特殊字符、未知信息、必填/类型校验、错误与空响应。

## 复用的公共契约

使用`core.schemas.SalesRequest/GenerationResponse`及`core.errors.ValidationError/CrossBotError/ErrorCode`。不复制公共数据类；无core契约变更，不需要其他分支同步新契约。模型消息采用服务常规role/content结构，业务资料在JSON对象中按命名字段访问，不依赖行号或列表中的业务字段位置。

## 离线验证

在本团队仓库根目录，用已安装依赖的Python执行：

```powershell
python -B -m unittest discover -s tests -p test_sales_transport.py -v
python -B -m unittest discover -s tests -v
python -m py_compile workflows/sales.py providers/contracts.py tests/test_sales_transport.py
```

本机实际使用外层`../../.venv/Scripts/python.exe`执行：W1新增9项通过，连同已有公共契约/存储测试共19项通过。全部mock，无网络请求，无密钥或客户资料。复用现有Python环境，未安装新依赖。运行语法检查通过，分支状态保持B分支。

## 输入输出示例

```python
from core.schemas import SalesRequest
from workflows.sales import generate_sales

class DemoClient:
    def complete(self, messages):
        # 这里只返回明确模拟结果；测试使用RecordingClient核对全部字段。
        return "【模拟草稿】请确认规格与所需资料。"

request = SalesRequest(
    inquiry="Need 500 pcs, USB lamps.\nShip to DE.",
    product_info="USB台灯，ABS材质\n颜色：白色",
    target_language="德文",
    customer_country="DE",
    requested_quantity=500,
)
result = generate_sales(request, DemoClient())
assert result.correlation_id == request.correlation_id
```

示例是模拟传输验证，不证明模型能生成德文或业务事实正确。输出附带人工核对提示，不表示规则校验通过。

## 已知限制和后续

本次只完成新工作流层。旧app.py/main.py尚未接入；旧界面的换行拆字段缺陷仍存在，不能用新模块的测试结果声称旧页面已修复。当前不执行结构化知识检索、合规规则检查、多轮状态机、真实API或流式事件；客户端接口只是可注入边界，不是已安装/可用的API服务。

SalesRequest暂没有独立交期字段，已知交期保留在原始inquiry/product_info里。未来需要单独结构化时先与A协调契约变更，不擅自扩展core。

下一步建议结合明确的多轮状态和统一事件，将W1接入页面，再接入知识来源和真正的provider；按用户逐步验收，不在旧业务目录添加新功能。W2及A/C/D代码未修改。本次尚未commit/push，不创建PR或合并。
