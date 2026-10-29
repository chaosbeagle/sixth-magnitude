# 六等星工作台

[English](README.md)

讓具備視覺能力的智能體，透過工具完成影像檢查。

六等星工作台（Sesta Workbench）以 Sixth Magnitude 框架為基礎，為前沿視覺模型提供通用影像檢查環境，適合搭配 Codex、Claude Code 這類智能體環境使用。給智能體一個任務與影像工作區，它就能在全貌與細節之間切換、標記區域、描述所見，並回到畫面核對結果。每筆觀察都連同影像與座標保存，之後可以重新開啟、修正或匯出。

框架結合簡短的操作技能、本機視覺工作台與 Python 工具，使用智能體環境既有的電腦操作和程式執行能力。模型負責視覺判斷，工具處理座標、影像擷取、量測與儲存。不同任務的知識可以寫進指引，沿用同一套操作與保存流程。

病理影像是第一個完整範例。全切片檢查需要在不同尺度查看組織、理解局部與周圍的關係，並留下病理醫師能夠檢查和修訂的觀察。通用工作台也接受一般圖片；肺病理設定則另外提供專用詞彙與量測。

![六等星視覺檢查流程](docs/images/overview.png)

## Sesta 與「六等星」的由來

Sesta 取自義大利文「第六」的陰性形式（[Treccani 詞典](https://www.treccani.it/vocabolario/sesta1/)）。產品名稱為 **Sesta Workbench／六等星工作台**；Sixth Magnitude 保留為框架名稱與天文意象。Plugin 使用短名 `sesta`，操作技能為 `inspect`。

這個名字起於 Astra 的 `low` 推理設定。在專案最初使用的設定中，從最高推理量往下數，`low` 正好排在第六級，於是有了天文上的聯想：六等星接近[黑暗天空下肉眼可見的極限](https://science.nasa.gov/solar-system/skywatching/night-sky-network/check-your-sky-quality-with-orion/)。

命名時想到的畫面，是許多微弱的光點逐漸連成星圖。在最初的病理流程裡，每個光點是一筆有位置、可供醫師檢查與校正的候選標註。框架讓這些小觀察累積下來，也讓人能回到每一筆觀察所依據的影像。

專案想探問的是：具備通用視覺能力的模型，在較低的推理設定下，搭配清楚的指引與代勞重複工作的工具，能完成多少有用的視覺任務？Astra `low` 是名字的起點；操作技能與工作台也可以交給其他模型和推理設定使用。

## 為什麼從 Astra low 出發？

專案起於一個觀察：GPT-6 Astra 即使使用 `low` thinking effort，仍保有很強的通用視覺能力。公開評測為這個觀察提供了具體依據。在 Artificial Analysis 的 MMMU-Pro 多模態學科推理評測中，Astra `low` 的成績高於前代 GPT-5.6 Sol 的 `high` 與 `max`：

| 模型 | 推理設定 | MMMU-Pro |
| --- | --- | ---: |
| [GPT-5.6 Sol](https://artificialanalysis.ai/models/gpt-5-6-sol-high) | high | 81.8% |
| [GPT-5.6 Sol](https://artificialanalysis.ai/models/gpt-5-6-sol) | max | 83.4% |
| [GPT-6 Astra](https://artificialanalysis.ai/models/gpt-6-astra-low) | low | **84.6%** |
| [GPT-6 Astra](https://artificialanalysis.ai/models/gpt-6-astra) | max | 86.9% |

OpenAI 公布的畫面定位與電腦操作評測也有提升：ScreenSpot-Pro 為 **92.7% 對 76.9%**，OSWorld 2.0 離線集的部分得分則為 **72.6% 對 65.7%**，皆為 Astra 與 Sol 的比較。這兩組數字採各推理設定中的最佳成績，並非 `low` 專屬分數。[來源與評測設定](https://openai.com/index/gpt-6-astra/)。

這些結果支持了六等星的設計方向：為已有視覺能力的模型提供工作區，讓它依所見採取行動、檢查操作結果，並留下可用的影像證據。評測說明設計的依據；WSI 展示則呈現實際操作流程，並不代表診斷準確度。[評測細節與延伸閱讀](docs/benchmarks.md)。

## 安裝到智能體環境

Codex：

```sh
codex plugin marketplace add chaosbeagle/sixth-magnitude
codex plugin add sesta@sixth-magnitude
```

Claude Code：

```sh
claude plugin marketplace add chaosbeagle/sixth-magnitude
claude plugin install sesta@sixth-magnitude
```

重新開啟對話，請智能體「**使用六等星工作台，在目前工作目錄建立影像檢查示範**」。Claude Code 也可使用 `/sesta:inspect`。Plugin 包含技能與工作台；首次設定時，智能體會將 Python 相依套件安裝到工作環境。[安裝與首次使用指南](docs/plugins.md)。

## 試用工作台

需要 Python 3.10 以上。在專案目錄執行：

```sh
python -m venv .venv
# 依照命令列環境啟用 .venv，再執行：
python -m pip install -e .
python -m sixth_magnitude demo --run runs/surface
python -m sixth_magnitude serve --run runs/surface --port 8790
```

開啟 **http://127.0.0.1:8790/**。範例會產生合成影像，不需要準備資料集或連接模型服務。

1. 選取或畫出一個區域。
2. 查看區域內部與周圍內容。
3. 描述可見的特徵並保存。
4. 檢查保存後的疊圖，再確認內容相符。
5. 匯出結果，或重新開啟工作區繼續操作。

範例工作區的觀察會與正式複核結果分開保存。工作台介面使用英文。

## 交給智能體操作

在智能體環境中載入[Sesta 的 inspect 技能](sixth_magnitude/skill/SKILL.md)，提供工作區路徑、瀏覽器網址與任務。例如：

> 檢查這張表面影像中的可見缺陷。為每個發現圈出區域並寫下簡短描述，保存後核對疊圖；不確定的部分保留待複核。

技能說明如何看圖、選擇尺度、保存發現與接續工作。瀏覽器提供視覺操作，本機工具負責影像處理及儲存。模型與推理等級在智能體環境中選擇，工作台不需要另外連接模型 API。

```sh
python -m sixth_magnitude prompt --run runs/surface
python -m sixth_magnitude status --run runs/surface
python -m sixth_magnitude export --run runs/surface
```

Plugin 與手動設定使用同一套技能和工作台。

## 全切片影像檢查

通用工作台可以開啟原生全切片影像。智能體可透過電腦操作檢查切片、保存區域，再回到帶有標註的影像核對結果。截圖使用通用流程操作公開病理影像。

![工作台中的全切片影像](docs/images/wsi-overview.jpg)

[病理操作指南](docs/pathology.md)說明影像登錄、細部視圖與量測定義；截圖所用影像的[來源與展示授權](docs/demo-sources.md)另外列出。圖中的觀察用於展示操作，仍是待專家複核的候選結果。

肺病理則有獨立的領域設定，加入型態欄位、批次型態領域與面積量測。可先使用內附示意圖熟悉這些操作：

```sh
python -m sixth_magnitude demo --profile lung-pathology --run runs/pathology
python -m sixth_magnitude serve --run runs/pathology --port 8791
```

## 使用自己的影像

依 [examples/sources.csv](examples/sources.csv) 建立 CSV，每張影像使用唯一代號。路徑可以相對於 CSV，也可以是本機絕對路徑。

```csv
asset_id,path,reader,mpp_x,mpp_y
surface_a,../data/surface.png,raster,,
```

```sh
python -m sixth_magnitude init --manifest examples/sources.csv --run runs/my-inspection
python -m sixth_magnitude serve --run runs/my-inspection
python -m sixth_magnitude export --run runs/my-inspection
```

內建讀取器支援 PNG、JPEG、WebP 與 BMP；原生全切片格式使用選配讀取器。沒有物理尺度的圖片使用像素座標，肺病理流程則需要尺度資料。

請勿把私人影像與工作資料夾提交到公開程式庫。重新開啟同一資料夾會接續已保存的工作，初始化則需要空資料夾。將既有工作區搬到另一台電腦時，影像仍須能從原登錄路徑讀取。

## 複核與匯出

每筆觀察會連同來源、幾何範圍、影像證據與複核狀態保存。修改文字或區域後，先前的視覺確認與人工接受狀態會清除；複核者可以在同一工作台修正內容。

可匯出 JSON、CSV、Parquet 與影像座標 GeoJSON，候選及已接受觀察分開輸出。工作台在本機供單一使用者操作，操作者在介面選擇角色。

- [架構說明](docs/architecture.md)
- [工作流程評估](docs/evaluation.md)
- [開發檢查](docs/validation.md)
- [參與開發](CONTRIBUTING.md)

## 資料與使用範圍

工作台在本機保存影像與觀察。智能體使用雲端模型查看工作區時，送出的截圖與其他內容會由該服務依你的帳號設定處理。請使用你有權提供給所選服務的影像。

病理範例供研究與工作流程開發使用。候選觀察在臨床使用前需要專家複核，專案尚未經過臨床驗證。本機伺服器請維持使用回送位址；介面中的角色用於標記操作，不具備使用者身分驗證功能。

六等星工作台是獨立專案。文中模型與智能體產品名稱用於指明討論的系統，不代表供應商背書。

## 授權

專案程式採用 [MIT 授權](LICENSE.md)，著作權 © 2026 Kuan Ting Wu。瀏覽器元件保留各自的[授權與聲明](THIRD_PARTY_NOTICES.md)；切片影像依照[來源條款](docs/demo-sources.md)使用。
