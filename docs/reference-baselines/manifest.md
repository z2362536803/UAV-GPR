# 参考项目基线 manifest

- 工具: `uav-gpr-reference-manifest` v1.0.0
- schema: `1.0`
- 生成时间 (UTC): 2026-08-21T19:06:51Z

## 钢筋仪软件开发 (`rebar-inspector`)

- 仓库路径: `E:\钢筋仪软件开发`
- branch: `feat/issue-16-pause-resume`
- HEAD: `938875234a99b47d78cfec940671005b63e9d15c`
- worktree dirty: True

### worktree status

```text
?? build_references.py
?? docs/ACQUISITION_DATA.md
?? docs/PRODUCT_SPECIFICATIONS.md
?? dual_channel_reference/
?? tmp/
?? 数据处理函数/dewow.py
?? 数据处理函数/flat_refelction_filter.py
```

### 来源角色与候选文件

| role | 文件 | tracked | SHA256 |
|---|---|---|---|
| bscan_ui | `src/rebar_inspector/ui/__init__.py` | committed | `81b25d9f6ccfca1841b4ad08b217128e8442fca45693652a58e59694925296bc` |
| bscan_ui | `src/rebar_inspector/ui/__main__.py` | committed | `d1b444adf00db3517dec33ecac717b981c4606d20878393c539dced3c422f2ff` |
| bscan_ui | `src/rebar_inspector/ui/acquisition.py` | committed | `f4badd68156b1fdd4cab52cfbd977c71b95e1d88996f92981ef016b94cee963a` |
| bscan_ui | `src/rebar_inspector/ui/app.py` | committed | `5285aa676efc4b1e56972142f7fddebefac29d60842715ff7fcf78a2ffd199df` |
| bscan_ui | `src/rebar_inspector/ui/colormap.py` | committed | `392330d2cdcce4d5774ebea320baa64465a6df800feaf23092cf427b358543f3` |
| bscan_ui | `src/rebar_inspector/ui/constants.py` | committed | `a72d9e7ddbf733e1ba00e68b9cbdb11863da9c79297187dbeee6567725f0df8f` |
| bscan_ui | `src/rebar_inspector/ui/fonts.py` | committed | `1fac322281ad4ee47bc53ec55bd82da746e7a37047fb162478105d9af76a92c8` |
| bscan_ui | `src/rebar_inspector/ui/main_window.py` | committed | `29245c8cc98af0eb18c34ee9200148e8442edd00cd1182c9f25b7b4face09379` |
| bscan_ui | `src/rebar_inspector/ui/reference_wizard.py` | committed | `c45fedc7c6b4868806a322f7cc2aca521d5171da33a4bf95544f994aad909d02` |
| calibration | `src/rebar_inspector/calibration/__init__.py` | committed | `0b5ed58170b7346484b65c500f6654322577c0fa2ee0b1c437413154e1a91258` |
| calibration | `src/rebar_inspector/calibration/_base.py` | committed | `8cd4fe95d3883283dbc4e8c10858bd972a4bc76c46f9a4b6ff79cc3c8590e8de` |
| calibration | `src/rebar_inspector/calibration/background.py` | committed | `db96c7ba287e6e90cd273e7d71abc4c1f2172135c135ab7c9c2d67bf33435c86` |
| calibration | `src/rebar_inspector/calibration/dual_reflection.py` | committed | `6f3725e571185325047a39352db662770f787fb3962136fc3d6986890ebf70a8` |
| calibration | `src/rebar_inspector/calibration/errors.py` | committed | `b6858162044d38220b5bb475304fd45aab6671701b017db07444378f1da77d47` |
| calibration | `src/rebar_inspector/calibration/interpolation.py` | committed | `9dc1b302be7fe9c0b67f293e79827a398fbb8f811312fcce8fa6625dcabf3da7` |
| calibration | `src/rebar_inspector/calibration/osl.py` | committed | `afbc6a73dedf261c223ca2c798adccaba19894bf4627e99fcc94cc38e0e5d3d9` |
| core | `src/rebar_inspector/core/__init__.py` | committed | `476a58b188854075f654b4ce5d3c1d3fa01d3088ead7b3f3e3edb258b9daf9f5` |
| core | `src/rebar_inspector/core/_arrays.py` | committed | `db69a18b939a5c29a2129119ba782cf8254b345aa9d23fe4199f9bf23037b90d` |
| core | `src/rebar_inspector/core/_frozen.py` | committed | `4893ef26889621fb6e2c014bb4a225c2556e7f787e6fe449a77187551c23f1bf` |
| core | `src/rebar_inspector/core/_scan_common.py` | committed | `b11b2139d8b89493a722df92b0ebc257c44308ba27e98f893db734d727c5a1ca` |
| core | `src/rebar_inspector/core/_serializable.py` | committed | `6f4fd12d2659dceeb04dccaa7846f62af510d46dbf8cd42a7a61576ac7615f03` |
| core | `src/rebar_inspector/core/channels.py` | committed | `cf0fb50543eb5400a10b66405b20f50a0e5af9e7494b580e61132af514309340` |
| core | `src/rebar_inspector/core/enums.py` | committed | `08129eb7e2419ad413754d19f5d5552c051df64ad60747d4ff990ff027866b19` |
| core | `src/rebar_inspector/core/frequency.py` | committed | `8164a64167bb223fd74523aecb6d73715cf0d36379d9a1ae4cee259c4c35badb` |
| core | `src/rebar_inspector/core/history.py` | committed | `077c8b2940cdfc7cf22f8c6c86ac7dc77596e3c10ca2af3afbdd00bbff23ba69` |
| core | `src/rebar_inspector/core/scan.py` | committed | `3f608405587b8f74f628673db06043df68ed3e233ecad5f0f3196b098714e03c` |
| core | `src/rebar_inspector/core/schema.py` | committed | `84a8d91a4ed4948be25d35baf28ba3be3b6b8ec1912e7731f46a134d50b94ae4` |
| core | `src/rebar_inspector/core/time_domain.py` | committed | `882a29113d02e94bb97be3030e3707374794eb546ee1840e12e434d0a5654597` |
| core | `src/rebar_inspector/core/trace.py` | committed | `a9f7ed31de4d3c4da8ece5bd3826d83a7f18861c2d04a8c35ff0767ce821edf2` |
| librevna | `src/rebar_inspector/acquisition/__init__.py` | committed | `838cbdc857d6e9f73b4dfb5ed461b7ba541768643a0e0c53e872734e7c31bcf7` |
| librevna | `src/rebar_inspector/acquisition/acquired.py` | committed | `44bf8c6adc76cfe0326048bf300942a67d8fdb49e7d0026bc5c78ed01a309626` |
| librevna | `src/rebar_inspector/acquisition/aggregation.py` | committed | `c8b64176f461f75a72809f0d072c09a31c752a3ede49a5d81543bfbf026126d1` |
| librevna | `src/rebar_inspector/acquisition/backend.py` | committed | `f05da35cdee84604d43945da8c30854a289fb7de36a90a3c46c110cf8ab3340f` |
| librevna | `src/rebar_inspector/acquisition/errors.py` | committed | `c3dfbfcaf4a6a5aea38f8ad79c4ecbbf546e69be2c7051dcf89ea1883aac2502` |
| librevna | `src/rebar_inspector/acquisition/file_replay.py` | committed | `96e4b1f57b5e400b29b91ea1820fc6883ec264a9be05c994b18a6ffa77cd29be` |
| librevna | `src/rebar_inspector/acquisition/librevna_protocol.py` | committed | `6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce` |
| librevna | `src/rebar_inspector/acquisition/librevna_usb.py` | committed | `a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87` |
| librevna | `src/rebar_inspector/acquisition/librevna_usb_transport.py` | committed | `7a2a1f87f81567d8955aa414e801b10a4fdb8e5bba79a7e9048e6b471095bb18` |
| librevna | `src/rebar_inspector/acquisition/simulated.py` | committed | `73749aa8a2435d193b8068dc9a3771f5021312a11589da19648cfedcb83a5af9` |
| librevna | `src/rebar_inspector/acquisition/sweep_config.py` | committed | `9877b7619747c07aeb7657ba3667322c2687396040bb00193afd5d8508c44801` |
| processing | `src/rebar_inspector/processing/__init__.py` | committed | `4b2dbc41bf71e550849390bcd16cd5fe7c0b7d5b3db8e2abb57d758ee111a2a4` |
| processing | `src/rebar_inspector/processing/_stage_common.py` | committed | `5ee8f31c709a9873e9e62579f0f4b9a75049e2c201ca14d0f4c2b37721a197a4` |
| processing | `src/rebar_inspector/processing/_time_stage_common.py` | committed | `e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81` |
| processing | `src/rebar_inspector/processing/background.py` | committed | `6685e8fcb00412074ff8329ab1145e7bb258a3bd8fe62ed7832e476d1c254397` |
| processing | `src/rebar_inspector/processing/bandpass.py` | committed | `3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51` |
| processing | `src/rebar_inspector/processing/dewow.py` | committed | `eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3a7f030e2c` |
| processing | `src/rebar_inspector/processing/dual_osl.py` | committed | `9a5ded94ec49477bd27838718594f2a2bdc3770aecd117ed22832bfd87f008e7` |
| processing | `src/rebar_inspector/processing/flat_reflection.py` | committed | `89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0` |
| processing | `src/rebar_inspector/processing/ifft.py` | committed | `9496288e9e918f788b88f41945ea5e43889cfb3c298cccf7543a33b5a41d297a` |
| processing | `src/rebar_inspector/processing/osl.py` | committed | `e5f9a788272807632a006559815fdc7804a037de82480fcd3dbdbc8ba7264f50` |
| processing | `src/rebar_inspector/processing/pipeline.py` | committed | `37b537463b6dd64f666720aa02215892cc24a2f243706d1d65061284bb6d9174` |
| storage | `src/rebar_inspector/storage/__init__.py` | committed | `dbd4d07144af85c7985d5f4d8852945318a21d12317e1aa9a1449d4b28542cc3` |
| storage | `src/rebar_inspector/storage/document.py` | committed | `a173d3ad31c51b4a162d5d53654b931f79019cb2c5d7716ff4fd1019863257da` |
| storage | `src/rebar_inspector/storage/errors.py` | committed | `abc52ea0dc98ecb248b97751f7ced169585cdf18a23e9c5317a0204cd5f286ec` |
| storage | `src/rebar_inspector/storage/rcscan.py` | committed | `290c5dadbbd74712096d5449084cb8b6b12e5bed557d0570b708cb883c46bc4c` |
| storage | `src/rebar_inspector/storage/reference_files.py` | committed | `970ea6a94739a3d240859993d7d433b1e755cc4c063c2273d331e5845a1bd3ce` |

### 明确排除内容

- 不迁移整个仓库、不复制大模块或巨型窗口
- application/reference_capture.py、application/scan_processing.py 等编排层（迁移具体 Issue 时按用途单独记录）
- docs/ 全部说明文档（本次仅冻结代码候选源）
- tests/ 全部测试（迁移时另行记录黄金样本来源）
- 根目录实测数据（*.rcscan/*.rcal/*.rcbg/*.npz/*.csv）与 calibration_reference/
- tmp/、dual_channel_reference/、LibreVNA采集速度测试/、读取函数/
- 数据处理函数/（MATLAB 与实验脚本，未跟踪）
- build_references.py（未跟踪脚本）
- .venv/ 与 __pycache__/

> 主要实现参考（AGENTS.md 2.1 / ADR-0005）：core 分层、LibreVNA 采集、校准、处理、storage、B-scan UI。此清单只覆盖 src/rebar_inspector 下的候选源文件；application/ 编排、docs/ 说明、tests/ 与实测数据不在此清单。
## UVA_GPR_system (`uav-gpr`)

- 仓库路径: `E:\UVA_GPR_system`
- branch: `my-modifications`
- HEAD: `194963a0472d1369a0911c24a6dacad5456158c8`
- worktree dirty: True

### worktree status

```text
 M run_ground_station.bat
?? "\346\225\260\346\215\256\345\244\204\347\220\206\345\207\275\346\225\260/"
?? "docs/LibreVNA\350\277\236\347\273\255\351\207\207\351\233\206\345\212\237\350\203\275\345\274\200\345\217\221\350\267\257\347\272\277\345\233\276.md"
?? "docs/LibreVNA\350\277\236\347\273\255\351\207\207\351\233\206\347\234\237\346\234\272\345\216\213\345\212\233\346\265\213\350\257\225\346\265\201\347\250\213.md"
?? "docs/\351\222\242\347\255\213\344\273\252\351\241\271\347\233\256_S11_S22_\351\207\207\351\233\206\344\270\216\346\240\241\345\207\206\345\217\202\350\200\203.md"
?? .npm-cache/
?? acquisition_scheduler.py
?? dsh-routing-suite-tmp/
?? dual_channel_reference/
?? librevna/field_test.py
?? librevna/librevna_continuous.py
?? librevna/librevna_errors.py
?? librevna/librevna_protocol.py
?? librevna/librevna_transport.py
?? reference_code/
?? run_air_station.bat
?? tests/test_acquisition_scheduler.py
?? tests/test_dual_channel_reference.py
?? tests/test_field_test.py
?? tests/test_librevna_backend.py
?? tests/test_librevna_continuous.py
?? tests/test_librevna_protocol.py
?? tests/test_librevna_transport.py
?? tests/test_rebar_inspector_s11_s22_reference.py
?? tests/test_uav_gpr_qt_continuous.py
?? tests/test_uav_gpr_qt_metadata.py
A  bscan_view.py
A  gnss_map_widget.py
A  gpr_processing.py
A  tests/test_bscan_gui.py
A  tests/test_bscan_view.py
A  tests/test_gnss_map_widget.py
A  tests/test_gpr_processing.py
A  tests/test_uav_gpr_qt_pipeline.py
MM uav_gpr_ground_station.py
MM uav_gpr_qt.py
```

### 来源角色与候选文件

| role | 文件 | tracked | SHA256 |
|---|---|---|---|
| gnss_parser_reader_matcher | `uav_gpr_qt.py` | staged_and_modified | `b94d3bfc42e8bf9d30247065e7b3bef52ae52cb6d4111cf8cac52e68b1cc13d2` |
| hm30_deployment_docs | `docs/HM30 v1.7.pdf` | committed | `7d0f2cf0501dbaa55282aac69f04d26b59b577e159d999090e843461b87d5aca` |
| hm30_deployment_docs | `docs/HM30 v1.7.txt` | committed | `c480650c42ba12b14844501640560dbf64cbc4bc80cdab0ecf40a67e2595fd3c` |
| hm30_deployment_docs | `docs/hm30_remote_operation.md` | committed | `ab0156aecd5285627810eee8accb76a3f3b1a901cb194408d96e7ee9121c4cbc` |
| hm30_deployment_docs | `docs/uav_gpr_remote_transmission_final_hm30_plan.md` | committed | `9a2d54f286e7baa64edb23caac28af1b44b1991a0af3087a87b1838a000d631b` |

### 明确排除内容

- uav_gpr_qt.py 内除 GGA/RMC 解析、GnssFixCache、GnssReader/Thread 与 sweep 匹配外的全部代码
- gnss_map_widget.py（在线 Leaflet/CDN 地图，禁止迁移）
- uav_gpr_ground_station.py、uav_gpr_receiver.py（主窗口与 NPZ/线上协议）
- bscan_view.py、gpr_processing.py（Matplotlib B-scan 与处理）
- acquisition_scheduler.py（调度实现；LibreVNA 调度以钢筋仪为权威）
- librevna/、reference_code/（LibreVNA 与 S11/S22 参考以钢筋仪为权威）
- gpr_runs/、remote_runs/、solt_2port_*.json（现场实测数据）
- tests/（本次不冻结旧测试，迁移时另行记录夹具来源）
- run_*.bat、*.log、.npm-cache/、dsh-routing-suite-tmp/、.venv*/
- 数据处理函数/（MATLAB 与实验脚本）
- docs/ 除 HM30 部署相关文档外的说明与报告

> 受限参考（AGENTS.md 2.2 / ADR-0005）：仅 GNSS parser/reader/matcher 契约与 HM30 部署文档。uav_gpr_qt.py 同一文件还包含未授权迁移的 UI/采集/存储代码，迁移时必须按行提取并重新审查；HM30 文档作为待复核的部署事实，不以文档陈述代替实物复核。
