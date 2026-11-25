"""Seed note for a fresh account, in the language the person signed in with.

The note lives on disk like any other note, so the user can rename or delete it. Only the
initial pick is localised; after that the file is theirs.
"""

from __future__ import annotations

# (file name, body) keyed by the same locale codes the front end uses.
NOTES: dict[str, tuple[str, str]] = {
    "en": (
        "Welcome.md",
        """# Welcome to Flow Notes

This note is a plain Markdown file in `data/notes/<your account>/`, so any editor can open it.

## A few tricks

- Type `/` at the start of a line to open the command menu
- `Ctrl + K` (or `Cmd + K`) opens full-text search
- Paste a screenshot: the image is stored in `data/uploads/<your account>/`
- Drag a note onto a folder to move it; the pencil renames or moves it as well
- Settings holds appearance, language, account and user management

## To-do

- [x] Create the first note
- [ ] Change the initial password in Settings
- [ ] Group notes into folders

## Code block

```python
from fastapi import FastAPI

app = FastAPI()
```

> The files are the truth; the index can be rebuilt at any time.
""",
    ),
    "zh-Hant": (
        "歡迎使用.md",
        """# 歡迎使用 Flow Notes

這篇筆記就是 `data/notes/<你的帳號>/` 底下的純文字 Markdown 檔，任何編輯器都能打開。

## 幾個小技巧

- 在行首輸入 `/` 開啟指令選單
- `Ctrl + K`（或 `Cmd + K`）開啟全文搜尋
- 直接貼上截圖，圖片會存到 `data/uploads/<你的帳號>/`
- 把筆記拖到資料夾上就能移動，鉛筆圖示也能改名或換資料夾
- 設定頁管理外觀、語言、帳號與使用者

## 待辦

- [x] 建立第一篇筆記
- [ ] 到設定頁改掉初始密碼
- [ ] 把筆記整理進資料夾

## 程式碼區塊

```python
from fastapi import FastAPI

app = FastAPI()
```

> 檔案才是真相，索引隨時可以重建。
""",
    ),
    "zh-Hans": (
        "欢迎使用.md",
        """# 欢迎使用 Flow Notes

这篇笔记就是 `data/notes/<你的账号>/` 下的纯文本 Markdown 文件，任何编辑器都能打开。

## 几个小技巧

- 在行首输入 `/` 打开命令菜单
- `Ctrl + K`（或 `Cmd + K`）打开全文搜索
- 直接粘贴截图，图片会存到 `data/uploads/<你的账号>/`
- 把笔记拖到文件夹上即可移动，铅笔图标也能改名或换文件夹
- 设置页管理外观、语言、账号与用户

## 待办

- [x] 创建第一篇笔记
- [ ] 到设置页改掉初始密码
- [ ] 把笔记整理进文件夹

## 代码块

```python
from fastapi import FastAPI

app = FastAPI()
```

> 文件才是真相，索引随时可以重建。
""",
    ),
    "ar": (
        "مرحبا.md",
        """# مرحبًا بك في Flow Notes

هذه الملاحظة ملف Markdown نصي داخل `data/notes/<حسابك>/`، ويمكن لأي محرر فتحه.

## بعض الاختصارات

- اكتب `/` في بداية السطر لفتح قائمة الأوامر
- `Ctrl + K` (أو `Cmd + K`) يفتح البحث الشامل
- الصق لقطة شاشة: تُحفظ الصورة في `data/uploads/<حسابك>/`
- اسحب الملاحظة إلى مجلد لنقلها، ويمكن لقلم التحرير تغيير الاسم أو المجلد
- الإعدادات تضم المظهر واللغة والحساب وإدارة المستخدمين

## المهام

- [x] إنشاء أول ملاحظة
- [ ] تغيير كلمة المرور الأولية من الإعدادات
- [ ] تنظيم الملاحظات في مجلدات

## كتلة برمجية

```python
from fastapi import FastAPI

app = FastAPI()
```

> الملفات هي المصدر، ويمكن إعادة بناء الفهرس في أي وقت.
""",
    ),
    "fr": (
        "Bienvenue.md",
        """# Bienvenue dans Flow Notes

Cette note est un simple fichier Markdown dans `data/notes/<votre compte>/`, ouvrable avec
n'importe quel éditeur.

## Quelques raccourcis

- Tapez `/` en début de ligne pour ouvrir le menu de commandes
- `Ctrl + K` (ou `Cmd + K`) ouvre la recherche plein texte
- Collez une capture d'écran : l'image est enregistrée dans `data/uploads/<votre compte>/`
- Faites glisser une note sur un dossier pour la déplacer ; le crayon la renomme ou la déplace
- Les réglages couvrent l'apparence, la langue, le compte et les utilisateurs

## À faire

- [x] Créer la première note
- [ ] Changer le mot de passe initial dans les réglages
- [ ] Ranger les notes dans des dossiers

## Bloc de code

```python
from fastapi import FastAPI

app = FastAPI()
```

> Les fichiers sont la vérité ; l'index peut être reconstruit à tout moment.
""",
    ),
    "ru": (
        "Добро-пожаловать.md",
        """# Добро пожаловать в Flow Notes

Эта заметка — обычный текстовый файл Markdown в `data/notes/<ваш аккаунт>/`, его откроет
любой редактор.

## Несколько приёмов

- Введите `/` в начале строки, чтобы открыть меню команд
- `Ctrl + K` (или `Cmd + K`) открывает полнотекстовый поиск
- Вставьте снимок экрана: изображение сохранится в `data/uploads/<ваш аккаунт>/`
- Перетащите заметку на папку, чтобы переместить её; карандаш тоже меняет имя и папку
- В настройках — оформление, язык, аккаунт и управление пользователями

## Задачи

- [x] Создать первую заметку
- [ ] Сменить начальный пароль в настройках
- [ ] Разложить заметки по папкам

## Блок кода

```python
from fastapi import FastAPI

app = FastAPI()
```

> Файлы — источник истины; индекс можно перестроить в любой момент.
""",
    ),
    "es": (
        "Bienvenido.md",
        """# Bienvenido a Flow Notes

Esta nota es un archivo Markdown de texto plano en `data/notes/<tu cuenta>/`, así que
cualquier editor puede abrirlo.

## Algunos atajos

- Escribe `/` al inicio de una línea para abrir el menú de comandos
- `Ctrl + K` (o `Cmd + K`) abre la búsqueda de texto completo
- Pega una captura de pantalla: la imagen se guarda en `data/uploads/<tu cuenta>/`
- Arrastra una nota sobre una carpeta para moverla; el lápiz también la renombra o la mueve
- Los ajustes reúnen apariencia, idioma, cuenta y gestión de usuarios

## Tareas

- [x] Crear la primera nota
- [ ] Cambiar la contraseña inicial en los ajustes
- [ ] Organizar las notas en carpetas

## Bloque de código

```python
from fastapi import FastAPI

app = FastAPI()
```

> Los archivos son la verdad; el índice se puede reconstruir en cualquier momento.
""",
    ),
}

DEFAULT_LOCALE = "en"

# Browsers send zh-TW / zh-HK / zh-CN style tags; map the ones we ship.
_ALIASES = {
    "zh": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans": "zh-Hans",
    "ar": "ar",
    "fr": "fr",
    "ru": "ru",
    "es": "es",
    "en": "en",
}


def normalise_locale(value: str | None) -> str:
    """Reduce a tag from localStorage or Accept-Language to one of the note locales."""
    raw = (value or "").strip().replace("_", "-").lower()
    if not raw:
        return DEFAULT_LOCALE
    # Accept-Language can be a list: "fr-CA,fr;q=0.9,en;q=0.8".
    for item in raw.split(","):
        tag = item.split(";")[0].strip()
        if not tag:
            continue
        if tag in _ALIASES:
            return _ALIASES[tag]
        base = tag.split("-")[0]
        if base in _ALIASES:
            return _ALIASES[base]
    return DEFAULT_LOCALE


def note_for(locale: str | None) -> tuple[str, str]:
    """File name and body of the seed note for a locale."""
    return NOTES[normalise_locale(locale)]
