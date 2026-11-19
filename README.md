# キーボード配列のPNG生成

`E-YOOSO_Z88_layout_v1.0.5.yaml` から、キーの形状とラベルを描いたPNGを2枚生成します。

## インストール

Python 3.11以降が必要です。WindowsのPowerShellで、このREADMEがあるフォルダを開いて実行します。

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

依存パッケージは `requirements.txt` に記載しています。

| パッケージ | 用途 |
| --- | --- |
| PyYAML | YAMLを読み込む |
| Pillow | 図形と文字を描き、PNGを保存する |
| Shapely | 多角形の検証、重複判定、角丸と隙間の計算に使う |
| fontTools | フォントにラベルの文字が収録されているか調べる |

## 画像の生成

仮想環境のPythonを指定して実行します。依存パッケージをインストール済みのPythonを使用する場合は、`python render_keyboard.py` でも実行できます。

```powershell
.\.venv\Scripts\python.exe render_keyboard.py
```

既定ではカレントディレクトリの `E-YOOSO_Z88_layout_v1.0.5.yaml` を読み込み、次のファイルを `output` に保存します。

- [main.png](output/main.png) — メインの79キー。JIS Enterの段差を含みます。
- [navigation_numpad.png](output/navigation_numpad.png) — 左の移動・編集13キーと右のテンキー17キー。元のグループ間隔と段の高さを保ちます。

入力ファイル名や出力先を変える場合は、パスを指定します。

```powershell
.\.venv\Scripts\python.exe render_keyboard.py --input layout.yaml --output output
```

## YAMLを編集して再生成

1. YAMLをUTF-8で開き、文字は `label`、位置は `x` と `y`、幅と高さは `w` と `h` を編集して保存します。`row` と `col` は並び順に使います。
2. 特殊形状の `outline` はキー左上からの相対座標です。外周の順に頂点を並べ、外接矩形が `w` と `h` に一致するようにします。数字や真偽値に見えるラベルは文字列として引用符で囲みます。
3. 生成コマンドを再実行します。キー数は実行時に表示され、同名のPNGが更新されます。重複ID、不正な座標・外周、面積のあるキーの重なりはエラーになります。キーの辺同士は接していても構いません。

各項目の意味とEnterの例は [YAMLの読み方](README_keyboard_layout.md) を参照してください。

## 描画設定とフォント

`render_keyboard.py` の先頭付近にある定数を変更してから再生成します。

| 定数 | 内容 |
| --- | --- |
| `PIXELS_PER_UNIT`, `SUPERSAMPLE` | 出力の解像度（既定160 px/u）と描画時の倍率（既定3倍） |
| `MARGIN`, `KEY_GAP` | 画像外周の余白とキーの間隔。単位はu |
| `CORNER_RADIUS`, `BORDER_WIDTH` | 角丸の半径と枠線の太さ。単位はu |
| `LABEL_PADDING`, `MAX_FONT_SIZE` | 文字と枠の間隔、およびフォントサイズの上限。単位はu |
| `BACKGROUND`, `KEY_COLOR`, `BORDER_COLOR`, `TEXT_COLOR` | 背景・キー・枠線・文字の色 |

キーの間隔は各キーを同じ量だけ内側に描いて確保します。小さなキーや細い外周で描画エラーになる場合は、間隔・角丸・枠線・文字の余白を小さくしてください。

日本語の太字フォントを自動で探します。Windowsではメイリオなど、macOSではヒラギノ角ゴシック、LinuxではNoto Sans CJK JPなどが候補です。必要な文字が見つからない場合は、日本語のTTF、OTF、TTCを指定できます。TTCは先頭の書体を使います。

```powershell
.\.venv\Scripts\python.exe render_keyboard.py --font "C:\Windows\Fonts\meiryob.ttc"
```

## 自動テスト

同じフォルダで実行します。付属YAMLの109キー、JIS Enterの段差、キーの寸法、ラベル、PNG上での文字の収まりを確認します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
