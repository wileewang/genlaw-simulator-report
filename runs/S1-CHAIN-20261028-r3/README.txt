S1 工艺卡输入包 / r1
主输入：process-card.pdf（已存档的三页工艺表达草案，含历史参数附件）和 target.step / target.brep。
process-plan.json 为人工核对的工步转录，geometry-observation.json 复用已有原生 CAD 观察；首版没有 PDF 自动识别或重新解析 CAD。
input.json 保留真实待补项：装夹/坐标和切削参数配置为空，应返回 missing_information，不生成执行方案。
input-software-reference.json 只用于程序测试，显式选择历史软件参考参数；不能作为工艺师傅批准或上机工艺。
requirements.json 的验收条件尚未确认。去毛刺、检验与后续处置保留为未验证。
原有 CAD、NC、仿真结果和冻结 r2 审阅包未改。
