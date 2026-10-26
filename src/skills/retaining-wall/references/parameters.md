# 适用范围和参数

仅支持墙高小于 5 m、干燥、水平无黏性填土、光滑直立墙背、斜前缘梯形或矩形墙、水平基底和非地震条件，按每延米墙长计算。可输入均布附加荷载。地下水、地震、黏性土、倾斜填土超出范围，应明确告知并停止按本模型计算；本轮没有新增这些工况的输入字段或程序化识别。

| 字段 | 含义与单位 |
|---|---|
| height_m | 墙高 H，m |
| base_width_m | 墙底宽 B，m |
| top_width_m | 墙顶宽 t，m |
| soil_unit_weight_kn_m3 | 填土重度 γ，kN/m³ |
| friction_angle_deg | 填土内摩擦角 φ，° |
| wall_unit_weight_kn_m3 | 墙体重度 γw，kN/m³ |
| base_friction_coefficient | 基底摩擦系数 μ |
| surcharge_kpa | 均布附加荷载 q，kPa；无附加荷载也须明确保存 0 |
