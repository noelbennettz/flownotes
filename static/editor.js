/**
 * Tiptap wrapper: Markdown in, Markdown out.
 *
 * Everything comes from static/vendor/editor.bundle.js (built locally by
 * vendor-build), so no network requests are made at runtime.
 */
import {
  Editor,
  StarterKit,
  TaskList,
  TaskItem,
  Image,
  Placeholder,
  Markdown,
} from "./vendor/editor.bundle.js";

import { t } from "./i18n.js";

const SLASH_ITEMS = [
  {
    key: "h1",
    labelKey: "editor.slash.h1",
    hint: "H1",
    keywords: ["h1", "heading", "title", "標題"],
    command: (chain) => chain.toggleHeading({ level: 1 }),
  },
  {
    key: "h2",
    labelKey: "editor.slash.h2",
    hint: "H2",
    keywords: ["h2", "heading", "標題"],
    command: (chain) => chain.toggleHeading({ level: 2 }),
  },
  {
    key: "h3",
    labelKey: "editor.slash.h3",
    hint: "H3",
    keywords: ["h3", "heading", "標題"],
    command: (chain) => chain.toggleHeading({ level: 3 }),
  },
  {
    key: "task",
    labelKey: "editor.slash.task",
    hint: "Task",
    keywords: ["task", "todo", "check", "待辦"],
    command: (chain) => chain.toggleTaskList(),
  },
  {
    key: "h4",
    labelKey: "editor.slash.h4",
    hint: "H4",
    keywords: ["h4", "heading", "標題"],
    command: (chain) => chain.toggleHeading({ level: 4 }),
  },
  {
    key: "h5",
    labelKey: "editor.slash.h5",
    hint: "H5",
    keywords: ["h5", "heading", "標題"],
    command: (chain) => chain.toggleHeading({ level: 5 }),
  },
  {
    key: "h6",
    labelKey: "editor.slash.h6",
    hint: "H6",
    keywords: ["h6", "heading", "標題"],
    command: (chain) => chain.toggleHeading({ level: 6 }),
  },
  {
    key: "bullet",
    labelKey: "editor.slash.bullet",
    hint: "List",
    keywords: ["bullet", "list", "ul", "清單"],
    command: (chain) => chain.toggleBulletList(),
  },
  {
    key: "ordered",
    labelKey: "editor.slash.ordered",
    hint: "1.",
    keywords: ["ordered", "number", "ol", "編號"],
    command: (chain) => chain.toggleOrderedList(),
  },
  {
    key: "code",
    labelKey: "editor.slash.code",
    hint: "Code",
    keywords: ["code", "pre", "程式"],
    command: (chain) => chain.toggleCodeBlock(),
  },
  {
    key: "quote",
    labelKey: "editor.slash.quote",
    hint: "Quote",
    keywords: ["quote", "blockquote", "引用"],
    command: (chain) => chain.toggleBlockquote(),
  },
  {
    key: "bold",
    labelKey: "editor.slash.bold",
    hint: "B",
    keywords: ["bold", "strong", "粗體"],
    command: (chain) => chain.toggleBold(),
  },
  {
    key: "italic",
    labelKey: "editor.slash.italic",
    hint: "I",
    keywords: ["italic", "em", "斜體"],
    command: (chain) => chain.toggleItalic(),
  },
  {
    key: "strike",
    labelKey: "editor.slash.strike",
    hint: "S",
    keywords: ["strike", "strikethrough", "del", "刪除線"],
    command: (chain) => chain.toggleStrike(),
  },
  {
    key: "inlineCode",
    labelKey: "editor.slash.inlineCode",
    hint: "`",
    keywords: ["code", "inline", "行內"],
    command: (chain) => chain.toggleCode(),
  },
  {
    key: "image",
    labelKey: "editor.slash.image",
    hint: "Img",
    keywords: ["image", "picture", "upload", "圖片"],
    action: "image",
  },
  {
    key: "divider",
    labelKey: "editor.slash.divider",
    hint: "---",
    keywords: ["divider", "hr", "line", "分隔"],
    command: (chain) => chain.setHorizontalRule(),
  },
];

const MENU_WIDTH = 216;
const MENU_MARGIN = 12;

/**
 * @param {object} options
 * @param {HTMLElement} options.element container element
 * @param {(markdown: string) => void} [options.onChange] called on every content change (programmatic loads excluded)
 * @param {(state: "start" | "done" | "error") => void} [options.onUpload] paste-upload state
 */
export function createEditor({ element, onChange = () => {}, onUpload = () => {} }) {
  const menu = document.createElement("div");
  menu.className = "slash-menu";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  document.body.appendChild(menu);

  let editor = null;
  let slash = null; // { from, query, items, index }
  let applyingExternal = false;

  const closeMenu = () => {
    slash = null;
    menu.hidden = true;
  };

  const applyItem = (item) => {
    if (!slash || !item) return;
    const range = { from: slash.from, to: editor.state.selection.from };
    closeMenu();
    if (item.action === "image") {
      editor.chain().focus().deleteRange(range).run();
      fileInput.click();
      return;
    }
    item.command(editor.chain().focus().deleteRange(range)).run();
  };

  const positionMenu = (from) => {
    const coords = editor.view.coordsAtPos(from);
    const rect = menu.getBoundingClientRect();
    const left = Math.max(MENU_MARGIN, Math.min(coords.left, window.innerWidth - MENU_WIDTH - MENU_MARGIN));
    let top = coords.bottom + 6;
    if (top + rect.height > window.innerHeight - MENU_MARGIN) {
      top = Math.max(MENU_MARGIN, coords.top - rect.height - 6);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  };

  const renderMenu = () => {
    if (!slash) return;
    menu.innerHTML = slash.items
      .map(
        (item, index) => `
        <button type="button" class="slash-menu__item${index === slash.index ? " is-active" : ""}"
                role="option" aria-selected="${index === slash.index}" data-index="${index}">
          <span>${t(item.labelKey)}</span>
          <span class="slash-menu__hint">${item.hint}</span>
        </button>`,
      )
      .join("");
    menu.hidden = false;
    positionMenu(slash.from);
  };

  const syncSlashMenu = () => {
    const { selection } = editor.state;
    if (!selection.empty) return closeMenu();

    const { $from } = selection;
    const blockName = $from.parent.type.name;
    if (blockName !== "paragraph" && blockName !== "heading") return closeMenu();

    // Only a leading slash or one after whitespace, so URLs and dates do not trigger it.
    const before = $from.parent.textBetween(0, $from.parentOffset, "\n", "\0");
    const match = /(?:^|\s)\/([^\s/]*)$/.exec(before);
    if (!match) return closeMenu();

    const query = match[1];
    const lowered = query.toLowerCase();
    const items = SLASH_ITEMS.filter(
      (item) =>
        t(item.labelKey).toLowerCase().includes(lowered) ||
        item.hint.toLowerCase().includes(lowered) ||
        item.keywords.some((word) => word.includes(lowered)),
    );
    if (items.length === 0) return closeMenu();

    const from = $from.pos - (query.length + 1);
    const previous = slash;
    const index = previous && previous.from === from ? Math.min(previous.index, items.length - 1) : 0;
    slash = { from, query, items, index };
    renderMenu();
  };

  const uploadImage = async (file) => {
    const form = new FormData();
    form.append("file", file, file.name || "pasted-image.png");
    onUpload("start");
    try {
      const response = await fetch("/api/upload", { method: "POST", body: form });
      if (!response.ok) throw new Error(`Upload failed (${response.status})`);
      const data = await response.json();
      if (!data.url) throw new Error("Upload response has no url");
      editor
        .chain()
        .focus()
        .setImage({ src: data.url, alt: file.name || "" })
        .run();
      onUpload("done");
    } catch (error) {
      console.error(error);
      onUpload("error");
    }
  };

  // Upload clipboard images instead of inlining base64 into the Markdown.
  const handlePaste = (_view, event) => {
    const clipboard = event.clipboardData;
    if (!clipboard) return false;
    const files = [];
    for (const item of clipboard.items || []) {
      if (item.kind === "file" && item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length === 0) return false;
    event.preventDefault();
    files.forEach((file) => {
      void uploadImage(file);
    });
    return true;
  };

  const handleKeyDown = (_view, event) => {
    if (menu.hidden || !slash) return false;
    if (event.key === "ArrowDown") {
      slash.index = (slash.index + 1) % slash.items.length;
      renderMenu();
      return true;
    }
    if (event.key === "ArrowUp") {
      slash.index = (slash.index - 1 + slash.items.length) % slash.items.length;
      renderMenu();
      return true;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      applyItem(slash.items[slash.index]);
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
      return true;
    }
    return false;
  };

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/png,image/jpeg,image/gif,image/webp,image/avif";
  fileInput.hidden = true;
  document.body.append(fileInput);
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) void uploadImage(file);
    fileInput.value = "";
  });

  editor = new Editor({
    element,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3, 4, 5, 6] } }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Image.configure({ inline: false, allowBase64: false }),
      Placeholder.configure({ placeholder: t("editor.placeholder") }),
      Markdown.configure({
        html: false,
        tightLists: true,
        breaks: false,
        linkify: false,
        transformPastedText: true,
        transformCopiedText: true,
      }),
    ],
    editorProps: {
      // dir="auto" keeps an English note left-to-right even when the UI is Arabic.
      attributes: { class: "tiptap", spellcheck: "false", "aria-label": "Note content", dir: "auto" },
      handlePaste,
      handleKeyDown,
    },
    onUpdate: () => {
      syncSlashMenu();
      if (applyingExternal) return;
      onChange(editor.storage.markdown.getMarkdown());
    },
    onBlur: () => closeMenu(),
  });

  menu.addEventListener("mousedown", (event) => {
    const target = event.target.closest(".slash-menu__item");
    if (!target) return;
    event.preventDefault();
    applyItem(slash && slash.items[Number(target.dataset.index)]);
  });

  const handleDocumentMouseDown = (event) => {
    if (menu.hidden) return;
    if (menu.contains(event.target)) return;
    closeMenu();
  };
  document.addEventListener("mousedown", handleDocumentMouseDown);

  const setMarkdown = (markdown) => {
    applyingExternal = true;
    try {
      if (markdown && markdown.trim()) {
        editor.commands.setContent(markdown, false);
      } else {
        // setContent runs the markdown parser, so pass neither "" nor HTML: clear the doc.
        editor.commands.clearContent(false);
      }
    } finally {
      applyingExternal = false;
    }
    closeMenu();
  };

  /** Re-render the placeholder after a language switch. */
  function setPlaceholder(text) {
    const extension = editor.extensionManager.extensions.find((item) => item.name === "placeholder");
    if (extension) extension.options.placeholder = text;
    // An empty transaction forces the decoration plugin to recompute.
    editor.view.dispatch(editor.state.tr.setMeta("addToHistory", false));
  }

  return {
    get editor() {
      return editor;
    },
    setMarkdown,
    setPlaceholder,
    getMarkdown: () => (editor ? editor.storage.markdown.getMarkdown() : ""),
    focus: () => editor.commands.focus("end"),
    setEditable: (value) => {
      editor.setEditable(value);
      if (!value) closeMenu();
    },
    destroy: () => {
      document.removeEventListener("mousedown", handleDocumentMouseDown);
      closeMenu();
      menu.remove();
      fileInput.remove();
      editor.destroy();
    },
  };
}
