# 魅族 BLE：Home Assistant 学习与遥控适配

当前版本 **1.5.0**，配合本仓库的 [ESPHome 外部组件](../../components/meizu_ble/README.md)使用。HA 原生 ESPHome 集成负责传感器、设备连接及固件更新；本适配提供学习向导、命名按键库和标准遥控动作。运行时无需 HA 主机蓝牙、原厂 App、APK、JNI 或 MQTT。

## 安装

1. 按[完整 YAML 示例](../../examples/esphome-meizu.yaml)安装 ESPHome，并在 HA 中添加该 ESPHome 节点。
2. 从 [v0.1.0 发布页](https://github.com/hallucigenia/esphome-meizu-ble/releases/tag/v0.1.0)下载 HA 适配 ZIP，或复制本目录至 HA `/config/custom_components/meizu_remoter_gateway/`。已有安装先备份组件目录和按键库，再覆盖源码并重启 HA。
3. 添加「魅族蓝牙网关」集成，选择「ESPHome BLE component」，选择 ESPHome 设备，填入实际原生动作名、API 来源名及小圆饼 MAC。动作名不含 `esphome.` 前缀；示例通常为 `meizu_gateway_meizu_puck`，API 来源名为 `meizu_puck`，以 HA 开发者工具中的实际名称为准。
4. 配置会验证一次只读状态往返，不发射红外。传感器直接使用原生 ESPHome 实体。

已有魅族条目使用「重新配置 → ESPHome」，选择同一只小圆饼，可保留配置条目、遥控实体身份和原按键库。集成域名继续为 `meizu_remoter_gateway`，不要改名后重复安装。旧 TCP 客户端保留用于兼容已有安装；本项目不发布旧独立网关固件。ESPHome 模式的蓝牙地址和轮询间隔在 YAML 中设置，固件更新使用 ESPHome。

## 学习

在集成「配置」中打开逐键向导，填写设备名与按键名。让原遥控器发射头对准小圆饼顶部中心，距离小于 1 厘米；提交后等待设备就绪提示，再短按按键。也可使用标准动作；示例实体 ID 请替换为自己的遥控实体：

```yaml
action: remote.learn_command
target:
  entity_id: remote.meizu_puck
data:
  device: tv
  command: power
  command_type: ir
  timeout: 15
```

只有收到设备 `0x07` 才开始 1–15 秒学习窗口。完整接收后，HA 与 ESP32 独立转换并核对结果，持久化成功后才保存。同名键须明确允许覆盖；失败保留旧码。学习和保存不发射，向导的「试发」会实际操作电器。

关闭向导会取消学习。标准动作可通过 `meizu_remoter_gateway.cancel_learning` 取消，参数 `serial_no` 使用遥控实体 `gateway_serial` 属性中的值。取消后调用者会收到明确结果，不会一直等待；学习期间其它控制请求返回忙。

## 发送与删除

```yaml
action: remote.send_command
target:
  entity_id: remote.meizu_puck
data:
  device: tv
  command: power
```

发送等待关联的原生 API 结果。只有缓存 `0x04` 或完整上传的 `0x02` 应答才标记 `confirmed: true`、`bridge_acknowledged: true`；超时不会自动重发。`last_ir_result` 保存最近结果。蓝牙确认不代表电器已改变状态。

使用 `remote.delete_command` 删除对应 `device` 和 `command`。按键保存在 HA `.storage/meizu_remoter_gateway.<entry_id>.commands`，包含原始捕获、完整发射码、缓存头和校验信息；它是用户运行数据，不属于发布源码。

`send_ir` 可直接发送 `serial_no`、`address`、`key` 和可选 `ir_code`，并返回动作响应。`store_command` 加上 `device` 与 `command` 后只保存、不发射。ESPHome 模式校验实际来源设备、API 来源、小圆饼 MAC 和请求编号，不以原生动作接受请求作为发射成功。

## 范围与许可

已完成单只 R16 的基础实机验证，包括真实采样、学码、上传与缓存发射、取消、超时、原生 OTA 和断电恢复。空调长码、其它型号和长期稳定性仍待验证。`codec.py` 无第三方依赖；测试波形只含通用按键名，没有设备身份或凭据。

保留 George Zhao 与 hallucigenia 的 [MIT 许可证和版权声明](LICENSE)。完整来源、ESPHome 依赖许可及开发检查见 [NOTICE](../../NOTICE.md) 和 [CONTRIBUTING](../../CONTRIBUTING.md)。
