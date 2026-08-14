import json
import os
import shutil
import threading
import tkinter as tk


from tkinter import filedialog
from tkinter import messagebox

from assistant import (
    CHARACTER_FILE,
    PERSONALITY_FILE,
    build_character_prompt,
)

from assistant_service import AssistantService
# ============================================================
# Optional Pillow support
# ============================================================

try:

    from PIL import Image
    from PIL import ImageDraw
    from PIL import ImageTk

    PIL_AVAILABLE = True

except ImportError:

    PIL_AVAILABLE = False


# ============================================================
# AIFren GUI
# ============================================================

WINDOW_WIDTH = 960
WINDOW_HEIGHT = 720


class AIFrenGUI:

    def __init__(
        self,
        root
    ):

        self.root = root

        self.root.title(
            "AIFren"
        )

        self.root.geometry(
            f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}"
        )

        self.root.minsize(
            760,
            560
        )

        self.root.configure(
            bg="#111113"
        )

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

        self.closed = False
        self.processing = False

        self.processing_lock = (
            threading.Lock()
        )

        # ----------------------------------------------------
        # Backend
        # ----------------------------------------------------

        self.llm = None
        self.memory = None
        self.conversation = None
        self.service = None
        self.voice = None
        self.character = None
        self.character_prompt = None
        self.tts = None
        self.ptt = None

        # ----------------------------------------------------
        # Personality
        # ----------------------------------------------------

        self.personality = ""

        # ----------------------------------------------------
        # Avatar
        # ----------------------------------------------------

        self.avatar_path = None
        self.header_avatar_image = None

        # ----------------------------------------------------
        # Settings window
        # ----------------------------------------------------

        self.settings_window = None

        # ----------------------------------------------------
        # Theme
        # ----------------------------------------------------

        self.bg = "#111113"
        self.panel = "#19191d"
        self.panel_light = "#202025"

        self.user_bubble = "#302b3a"
        self.assistant_bubble = "#222226"

        self.text = "#f4f4f5"
        self.secondary = "#a1a1aa"
        self.muted = "#71717a"

        self.accent = "#8b5cf6"
        self.accent_hover = "#9d75f5"

        self.success = "#86efac"
        self.listening = "#f87171"
        self.thinking = "#facc15"

        # ----------------------------------------------------
        # Build
        # ----------------------------------------------------

        self.build_ui()

        self.set_status(
            "Starting AIFren...",
            "normal"
        )

        self.set_controls_enabled(
            False
        )

        threading.Thread(
            target=self.initialize_backend,
            daemon=True
        ).start()

    # ========================================================
    # UI
    # ========================================================

    def build_ui(
        self
    ):

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        header = tk.Frame(
            self.root,
            bg=self.panel,
            height=74
        )

        header.pack(
            fill="x"
        )

        header.pack_propagate(
            False
        )

        self.avatar = tk.Label(
            header,
            text="●",
            font=(
                "Segoe UI",
                22
            ),
            fg=self.accent,
            bg=self.panel
        )

        self.avatar.pack(
            side="left",
            padx=20
        )

        title_frame = tk.Frame(
            header,
            bg=self.panel
        )

        title_frame.pack(
            side="left",
            fill="y"
        )

        self.title_label = tk.Label(
            title_frame,
            text="Message window",
            font=(
                "Segoe UI",
                18,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        )

        self.title_label.pack(
            anchor="w",
            pady=(12, 0)
        )

        self.character_label = tk.Label(
            title_frame,
            text="Initializing...",
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel
        )

        self.character_label.pack(
            anchor="w"
        )

        # ----------------------------------------------------
        # Settings button
        # ----------------------------------------------------

        self.settings_button = tk.Button(
            header,
            text="⚙",
            command=self.open_settings,
            font=(
                "Segoe UI",
                16
            ),
            fg=self.secondary,
            bg=self.panel,
            activebackground=self.panel,
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            cursor="hand2"
        )

        self.settings_button.pack(
            side="right",
            padx=12
        )

        self.header_status = tk.Label(
            header,
            text="● Offline",
            font=(
                "Segoe UI",
                9
            ),
            fg=self.muted,
            bg=self.panel
        )

        self.header_status.pack(
            side="right",
            padx=12
        )

        # ----------------------------------------------------
        # Chat
        # ----------------------------------------------------

        chat_outer = tk.Frame(
            self.root,
            bg=self.bg
        )

        chat_outer.pack(
            fill="both",
            expand=True,
            padx=18,
            pady=14
        )

        self.chat_canvas = tk.Canvas(
            chat_outer,
            bg=self.bg,
            highlightthickness=0,
            borderwidth=0
        )

        self.chat_canvas.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar = tk.Scrollbar(
            chat_outer,
            orient="vertical",
            command=self.chat_canvas.yview
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        self.chat_canvas.configure(
            yscrollcommand=scrollbar.set
        )

        self.chat_frame = tk.Frame(
            self.chat_canvas,
            bg=self.bg
        )

        self.chat_window = (
            self.chat_canvas.create_window(
                (
                    0,
                    0
                ),
                window=self.chat_frame,
                anchor="nw"
            )
        )

        self.chat_frame.bind(
            "<Configure>",
            self._chat_configure
        )

        self.chat_canvas.bind(
            "<Configure>",
            self._canvas_configure
        )

        self.chat_canvas.bind_all(
            "<MouseWheel>",
            self._mousewheel
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        status_frame = tk.Frame(
            self.root,
            bg=self.panel,
            height=48
        )

        status_frame.pack(
            fill="x"
        )

        status_frame.pack_propagate(
            False
        )

        self.status_dot = tk.Label(
            status_frame,
            text="●",
            font=(
                "Segoe UI",
                11
            ),
            fg=self.muted,
            bg=self.panel
        )

        self.status_dot.pack(
            side="left",
            padx=(18, 7)
        )

        self.status_label = tk.Label(
            status_frame,
            text="Starting...",
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel
        )

        self.status_label.pack(
            side="left"
        )

        # ----------------------------------------------------
        # Input
        # ----------------------------------------------------

        input_frame = tk.Frame(
            self.root,
            bg=self.panel_light,
            padx=14,
            pady=12
        )

        input_frame.pack(
            fill="x"
        )

        self.input_entry = tk.Entry(
            input_frame,
            font=(
                "Segoe UI",
                11
            ),
            bg="#29292e",
            fg=self.text,
            insertbackground=self.text,
            relief="flat",
            borderwidth=0
        )

        self.input_entry.pack(
            side="left",
            fill="x",
            expand=True,
            ipady=12,
            padx=(4, 10)
        )

        self.input_entry.bind(
            "<Return>",
            self.send_message
        )

        self.send_button = tk.Button(
            input_frame,
            text="Send",
            command=self.send_message,
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            fg="white",
            bg=self.accent,
            activebackground=self.accent_hover,
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            padx=24,
            pady=10,
            cursor="hand2"
        )

        self.send_button.pack(
            side="right"
        )

        # ----------------------------------------------------
        # Bottom controls
        # ----------------------------------------------------

        controls = tk.Frame(
            self.root,
            bg=self.bg,
            height=62
        )

        controls.pack(
            fill="x"
        )

        controls.pack_propagate(
            False
        )

        self.ptt_indicator = tk.Label(
            controls,
            text="🎙",
            font=(
                "Segoe UI",
                18
            ),
            fg=self.secondary,
            bg=self.bg
        )

        self.ptt_indicator.pack(
            side="left",
            padx=(20, 7)
        )

        # No "Hold F8 to talk" text here.
        # You specifically removed that earlier.

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        volume_label = tk.Label(
            controls,
            text="🔊",
            font=(
                "Segoe UI",
                11
            ),
            fg=self.secondary,
            bg=self.bg
        )

        volume_label.pack(
            side="right",
            padx=(10, 4)
        )

        self.volume_value = tk.Label(
            controls,
            text="100%",
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.bg,
            width=5
        )

        self.volume_value.pack(
            side="right"
        )

        self.volume_slider = tk.Scale(
            controls,
            from_=0,
            to=100,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            length=130,
            bg=self.bg,
            fg=self.secondary,
            highlightthickness=0,
            troughcolor="#303035",
            activebackground=self.accent,
            command=self.change_volume
        )

        self.volume_slider.set(
            100
        )

        self.volume_slider.pack(
            side="right",
            padx=5
        )

        # ----------------------------------------------------
        # Stop
        # ----------------------------------------------------

        self.stop_button = tk.Button(
            controls,
            text="Stop",
            command=self.stop_speaking,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.bg,
            activebackground=self.bg,
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            cursor="hand2"
        )

        self.stop_button.pack(
            side="right",
            padx=15
        )
        
    def clean_text_for_tts(self, text):
        """
        Remove roleplay emotes/actions from text before TTS.
    
        Example:
            "*Serval smiles.* Hello!"
        becomes:
            "Hello!"
        """
    
        if not text:
            return ""
    
        # Remove *emotes/actions*
        text = re.sub(
            r"\*[^*]*\*",
            "",
            text
        )
    
        # Remove _emotes/actions_ if used
        text = re.sub(
            r"_([^_]+)_",
            "",
            text
        )
    
        # Clean up excessive whitespace
        text = re.sub(
            r"\s+",
            " ",
            text
        )
    
        return text.strip()

    # ========================================================
    # Canvas
    # ========================================================

    def _chat_configure(
        self,
        event=None
    ):

        self.root.update_idletasks()

        bbox = self.chat_canvas.bbox(
            "all"
        )

        if bbox:

            self.chat_canvas.configure(
                scrollregion=bbox
            )

    def _canvas_configure(
        self,
        event
    ):

        self.chat_canvas.itemconfigure(
            self.chat_window,
            width=event.width
        )

        self.root.after_idle(
            self._update_scrollregion
        )

    def _update_scrollregion(
        self
    ):

        bbox = self.chat_canvas.bbox(
            "all"
        )

        if bbox:

            self.chat_canvas.configure(
                scrollregion=bbox
            )

    def _mousewheel(
        self,
        event
    ):

        if self.chat_canvas.winfo_exists():

            self.chat_canvas.yview_scroll(
                int(
                    -1
                    * (event.delta / 120)
                ),
                "units"
            )

    def scroll_to_bottom(
        self
    ):

        if self.closed:

            return

        self.root.update_idletasks()

        self._update_scrollregion()

        self.chat_canvas.yview_moveto(
            1.0
        )

    # ========================================================
    # Backend initialization
    # ========================================================

    def initialize_backend(
        self
    ):

        try:

            self.service = AssistantService.create_default()

            self.llm = self.service.llm
            self.memory = self.service.memory
            self.conversation = self.service.conversation
            self.voice = self.service.voice
            self.character = self.service.character
            self.character_prompt = self.service.character_prompt
            self.tts = self.service.tts

            self.service.subscribe(
                self.handle_backend_event
            )

            # ------------------------------------------------
            # Load personality for the settings editor.
            # ------------------------------------------------

            self.load_personality()

            # ------------------------------------------------
            # Load avatar.
            # ------------------------------------------------

            self.load_avatar()

            self.ptt = self.service.start_push_to_talk()

            self.root.after(
                0,
                self.backend_ready
            )

        except Exception as error:

            self.root.after(
                0,
                lambda error=error:
                self.backend_error(
                    error
                )
            )

    # ========================================================
    # Personality
    # ========================================================

    def load_personality(
        self
    ):

        try:

            with open(
                PERSONALITY_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                self.personality = file.read()

        except Exception:

            self.personality = ""

    # ========================================================
    # Character files
    # ========================================================

    def save_character_files(
        self,
        name,
        description,
        personality
    ):

        # ----------------------------------------------------
        # Read existing character.json so we preserve all
        # fields that already exist.
        # ----------------------------------------------------

        try:

            with open(
                CHARACTER_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                character_data = json.load(
                    file
                )

        except Exception:

            character_data = {}

        if not isinstance(
            character_data,
            dict
        ):

            character_data = {}

        character_data["name"] = name
        character_data["description"] = description

        # ----------------------------------------------------
        # Save character.json.
        # ----------------------------------------------------

        with open(
            CHARACTER_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                character_data,
                file,
                indent=4,
                ensure_ascii=False
            )

        # ----------------------------------------------------
        # Save personality.md.
        # ----------------------------------------------------

        with open(
            PERSONALITY_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                personality
            )

        # ----------------------------------------------------
        # Update the running objects.
        # ----------------------------------------------------

        self.character = character_data

        self.personality = personality

        self.character_prompt = (
            build_character_prompt(
                self.character,
                self.personality
            )
        )

        if self.service:

            self.service.character = self.character
            self.service.character_prompt = self.character_prompt

    # ========================================================
    # Avatar path
    # ========================================================

    def get_avatar_path(
        self
    ):

        if not self.character:

            return None

        avatar_data = self.character.get(
            "avatar",
            {}
        )

        if not isinstance(
            avatar_data,
            dict
        ):

            avatar_data = {}

        configured_path = avatar_data.get(
            "path"
        )

        if configured_path:

            if os.path.isabs(
                configured_path
            ):

                path = configured_path

            else:

                path = os.path.join(
                    os.path.dirname(
                        CHARACTER_FILE
                    ),
                    configured_path
                )

            if os.path.isfile(
                path
            ):

                return path

        # ----------------------------------------------------
        # Also check the standard avatar filenames.
        # ----------------------------------------------------

        character_directory = os.path.dirname(
            CHARACTER_FILE
        )

        for filename in (
            "avatar.png",
            "avatar.jpg",
            "avatar.jpeg",
            "avatar.gif"
        ):

            path = os.path.join(
                character_directory,
                filename
            )

            if os.path.isfile(
                path
            ):

                return path

        return None

    # ========================================================
    # Avatar loading
    # ========================================================

    def load_avatar(
        self
    ):

        self.avatar_path = (
            self.get_avatar_path()
        )

        if not self.avatar_path:

            self.avatar.configure(
                image="",
                text="●"
            )

            self.header_avatar_image = None

            return

        if not PIL_AVAILABLE:

            self.avatar.configure(
                image="",
                text="●"
            )

            self.header_avatar_image = None

            return

        try:

            image = Image.open(
                self.avatar_path
            ).convert(
                "RGBA"
            )

            image = image.resize(
                (
                    48,
                    48
                ),
                Image.Resampling.LANCZOS
            )

            mask = Image.new(
                "L",
                (
                    48,
                    48
                ),
                0
            )

            draw = ImageDraw.Draw(
                mask
            )

            draw.ellipse(
                (
                    0,
                    0,
                    47,
                    47
                ),
                fill=255
            )

            image.putalpha(
                mask
            )

            self.header_avatar_image = (
                ImageTk.PhotoImage(
                    image
                )
            )

            self.avatar.configure(
                image=self.header_avatar_image,
                text=""
            )

        except Exception as error:

            print(
                f"Avatar load error: {error}"
            )

            self.avatar.configure(
                image="",
                text="●"
            )

            self.header_avatar_image = None

    # ========================================================
    # Settings
    # ========================================================

    def open_settings(
        self
    ):

        if self.settings_window:

            try:

                if self.settings_window.winfo_exists():

                    self.settings_window.lift()
                    self.settings_window.focus_force()

                    return

            except Exception:

                self.settings_window = None

        window = tk.Toplevel(
            self.root
        )

        self.settings_window = window

        window.title(
            "AIFren Settings"
        )

        window.geometry(
            "760x700"
        )

        window.minsize(
            600,
            500
        )

        window.configure(
            bg=self.panel
        )

        window.transient(
            self.root
        )

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        header = tk.Frame(
            window,
            bg=self.panel,
            padx=22,
            pady=18
        )

        header.pack(
            fill="x"
        )

        tk.Label(
            header,
            text="Character Settings",
            font=(
                "Segoe UI",
                18,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            anchor="w"
        )

        tk.Label(
            header,
            text="Changes are saved immediately to your character files.",
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel
        ).pack(
            anchor="w",
            pady=(3, 0)
        )

        # ----------------------------------------------------
        # Main
        # ----------------------------------------------------

        main = tk.Frame(
            window,
            bg=self.panel,
            padx=22,
            pady=5
        )

        main.pack(
            fill="both",
            expand=True
        )

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        tk.Label(
            main,
            text="Character name",
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            anchor="w"
        )

        name_entry = tk.Entry(
            main,
            font=(
                "Segoe UI",
                11
            ),
            bg="#29292e",
            fg=self.text,
            insertbackground=self.text,
            relief="flat"
        )

        name_entry.pack(
            fill="x",
            pady=(5, 12),
            ipady=8
        )

        name_entry.insert(
            0,
            self.character.get(
                "name",
                "AIFren"
            )
            if self.character
            else "AIFren"
        )

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        tk.Label(
            main,
            text="Description",
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            anchor="w"
        )

        description_text = tk.Text(
            main,
            height=4,
            font=(
                "Segoe UI",
                10
            ),
            bg="#29292e",
            fg=self.text,
            insertbackground=self.text,
            relief="flat",
            wrap="word"
        )

        description_text.pack(
            fill="x",
            pady=(5, 12)
        )

        description_text.insert(
            "1.0",
            self.character.get(
                "description",
                ""
            )
            if self.character
            else ""
        )

        # ----------------------------------------------------
        # Personality
        # ----------------------------------------------------

        tk.Label(
            main,
            text="Personality",
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            anchor="w"
        )

        personality_frame = tk.Frame(
            main,
            bg=self.panel
        )

        personality_frame.pack(
            fill="both",
            expand=True,
            pady=(5, 8)
        )

        personality_text = tk.Text(
            personality_frame,
            font=(
                "Consolas",
                10
            ),
            bg="#202025",
            fg=self.text,
            insertbackground=self.text,
            relief="flat",
            wrap="word",
            undo=True
        )

        personality_text.pack(
            side="left",
            fill="both",
            expand=True
        )

        personality_scroll = tk.Scrollbar(
            personality_frame,
            command=personality_text.yview
        )

        personality_scroll.pack(
            side="right",
            fill="y"
        )

        personality_text.configure(
            yscrollcommand=personality_scroll.set
        )

        personality_text.insert(
            "1.0",
            self.personality
        )

        # ----------------------------------------------------
        # Avatar controls
        # ----------------------------------------------------

        avatar_frame = tk.Frame(
            main,
            bg=self.panel
        )

        avatar_frame.pack(
            fill="x",
            pady=(3, 8)
        )

        tk.Label(
            avatar_frame,
            text="Avatar",
            font=(
                "Segoe UI",
                10,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            side="left"
        )

        avatar_name = tk.Label(
            avatar_frame,
            text=(
                os.path.basename(
                    self.avatar_path
                )
                if self.avatar_path
                else "No avatar selected"
            ),
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel
        )

        avatar_name.pack(
            side="left",
            padx=12
        )

        def choose_avatar():

            if not PIL_AVAILABLE:

                messagebox.showwarning(
                    "Pillow Required",
                    (
                        "Pillow is required for character "
                        "avatars.\n\n"
                        "Install it with:\n"
                        "python -m pip install Pillow"
                    ),
                    parent=window
                )

                return

            source = filedialog.askopenfilename(
                parent=window,
                title="Choose Character Avatar",
                filetypes=[
                    (
                        "Image files",
                        "*.png *.jpg *.jpeg *.gif"
                    ),
                    (
                        "All files",
                        "*.*"
                    )
                ]
            )

            if not source:

                return

            try:

                character_directory = os.path.dirname(
                    CHARACTER_FILE
                )

                os.makedirs(
                    character_directory,
                    exist_ok=True
                )

                # ------------------------------------------------
                # Verify that Pillow can open the image.
                # ------------------------------------------------

                test_image = Image.open(
                    source
                )

                test_image.verify()

                extension = (
                    os.path.splitext(
                        source
                    )[1].lower()
                )

                if extension not in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif"
                ):

                    raise ValueError(
                        "Please select a PNG, JPG, JPEG, or GIF."
                    )

                # ------------------------------------------------
                # Normalize the filename.
                # ------------------------------------------------

                destination = os.path.join(
                    character_directory,
                    "avatar" + extension
                )

                shutil.copy2(
                    source,
                    destination
                )

                # ------------------------------------------------
                # Remove old avatar extensions.
                # ------------------------------------------------

                for old_extension in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif"
                ):

                    old_path = os.path.join(
                        character_directory,
                        "avatar" + old_extension
                    )

                    if (
                        old_path != destination
                        and os.path.isfile(old_path)
                    ):

                        try:

                            os.remove(
                                old_path
                            )

                        except OSError:

                            pass

                # ------------------------------------------------
                # Update character data.
                # ------------------------------------------------

                avatar_data = self.character.get(
                    "avatar",
                    {}
                )

                if not isinstance(
                    avatar_data,
                    dict
                ):

                    avatar_data = {}

                avatar_data["enabled"] = True
                avatar_data["path"] = (
                    os.path.basename(
                        destination
                    )
                )

                self.character["avatar"] = (
                    avatar_data
                )

                with open(
                    CHARACTER_FILE,
                    "w",
                    encoding="utf-8"
                ) as file:

                    json.dump(
                        self.character,
                        file,
                        indent=4,
                        ensure_ascii=False
                    )

                self.avatar_path = destination

                avatar_name.configure(
                    text=os.path.basename(
                        destination
                    )
                )

                self.load_avatar()

                # ------------------------------------------------
                # Refresh existing bubbles isn't necessary because
                # new bubbles will use the new avatar.
                # ------------------------------------------------

            except Exception as error:

                messagebox.showerror(
                    "Avatar Error",
                    str(error),
                    parent=window
                )

        tk.Button(
            avatar_frame,
            text="Choose...",
            command=choose_avatar,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.text,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=5,
            cursor="hand2"
        ).pack(
            side="right"
        )

        # ----------------------------------------------------
        # Bottom buttons
        # ----------------------------------------------------

        buttons = tk.Frame(
            window,
            bg=self.panel,
            padx=22,
            pady=16
        )

        buttons.pack(
            fill="x"
        )

        def cancel():

            self.settings_window = None

            window.destroy()

        def save():

            try:

                name = (
                    name_entry
                    .get()
                    .strip()
                )

                if not name:

                    name = "AIFren"

                description = (
                    description_text
                    .get(
                        "1.0",
                        "end-1c"
                    )
                )

                personality = (
                    personality_text
                    .get(
                        "1.0",
                        "end-1c"
                    )
                )

                self.save_character_files(
                    name,
                    description,
                    personality
                )

                self.character_label.configure(
                    text=name
                )

                self.load_avatar()

                self.settings_window = None

                window.destroy()

                self.set_status(
                    "Character settings saved.",
                    "ready"
                )

            except Exception as error:

                messagebox.showerror(
                    "Save Error",
                    str(error),
                    parent=window
                )

        tk.Button(
            buttons,
            text="🧠 Memory",
            command=self.open_memory_manager,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.text,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=8,
            cursor="hand2"
        ).pack(
            side="left"
        )


        tk.Button(
            buttons,
            text="Cancel",
            command=cancel,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=8,
            cursor="hand2"
        ).pack(
            side="right",
            padx=(8, 0)
        )

        tk.Button(
            buttons,
            text="Save Changes",
            command=save,
            font=(
                "Segoe UI",
                9,
                "bold"
            ),
            fg="white",
            bg=self.accent,
            activebackground=self.accent_hover,
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=8,
            cursor="hand2"
        ).pack(
            side="right"
        )

        window.protocol(
            "WM_DELETE_WINDOW",
            cancel
        )

    # ========================================================
    # Memory Manager
    # ========================================================

    def open_memory_manager(
        self
    ):

        if not self.memory:

            messagebox.showwarning(
                "Memory",
                "The memory system is not ready yet.",
                parent=self.root
            )

            return

        window = tk.Toplevel(
            self.root
        )

        window.title(
            "AIFren Memory"
        )

        window.geometry(
            "760x620"
        )

        window.minsize(
            600,
            450
        )

        window.configure(
            bg=self.panel
        )

        window.transient(
            self.root
        )

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        header = tk.Frame(
            window,
            bg=self.panel,
            padx=20,
            pady=16
        )

        header.pack(
            fill="x"
        )

        tk.Label(
            header,
            text="Long-Term Memory",
            font=(
                "Segoe UI",
                17,
                "bold"
            ),
            fg=self.text,
            bg=self.panel
        ).pack(
            anchor="w"
        )

        tk.Label(
            header,
            text=(
                "View and edit what the character remembers."
            ),
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel
        ).pack(
            anchor="w",
            pady=(3, 0)
        )

        # ----------------------------------------------------
        # Search
        # ----------------------------------------------------

        search_frame = tk.Frame(
            window,
            bg=self.panel,
            padx=20
        )

        search_frame.pack(
            fill="x",
            pady=(0, 10)
        )

        search_entry = tk.Entry(
            search_frame,
            font=(
                "Segoe UI",
                10
            ),
            bg="#29292e",
            fg=self.text,
            insertbackground=self.text,
            relief="flat"
        )

        search_entry.pack(
            side="left",
            fill="x",
            expand=True,
            ipady=7
        )

        # ----------------------------------------------------
        # Memory list
        # ----------------------------------------------------

        list_frame = tk.Frame(
            window,
            bg=self.panel,
            padx=20
        )

        list_frame.pack(
            fill="both",
            expand=True
        )

        memory_list = tk.Listbox(
            list_frame,
            font=(
                "Segoe UI",
                10
            ),
            bg="#202025",
            fg=self.text,
            selectbackground=self.accent,
            selectforeground="white",
            relief="flat",
            borderwidth=0,
            activestyle="none"
        )

        memory_list.pack(
            side="left",
            fill="both",
            expand=True
        )

        memory_scroll = tk.Scrollbar(
            list_frame,
            command=memory_list.yview
        )

        memory_scroll.pack(
            side="right",
            fill="y"
        )

        memory_list.configure(
            yscrollcommand=memory_scroll.set
        )

        # ----------------------------------------------------
        # Keep references to filtered memories
        # ----------------------------------------------------

        displayed_memories = []

        def refresh_list():

            displayed_memories.clear()

            memory_list.delete(
                0,
                "end"
            )

            memories = getattr(
                self.memory,
                "memories",
                []
            )

            search_term = (
                search_entry
                .get()
                .strip()
                .lower()
            )

            for memory in memories:

                if not isinstance(
                    memory,
                    dict
                ):

                    continue

                content = str(
                    memory.get(
                        "content",
                        ""
                    )
                )

                category = str(
                    memory.get(
                        "category",
                        "unknown"
                    )
                )

                if search_term:

                    searchable = (
                        content
                        + " "
                        + category
                    ).lower()

                    if search_term not in searchable:

                        continue

                displayed_memories.append(
                    memory
                )

                memory_id = memory.get(
                    "id",
                    "?"
                )

                importance = memory.get(
                    "importance",
                    "?"
                )

                memory_list.insert(
                    "end",
                    (
                        f"[{memory_id}] "
                        f"[{category}] "
                        f"(importance {importance}) "
                        f"{content}"
                    )
                )

        # ----------------------------------------------------
        # Edit
        # ----------------------------------------------------

        def edit_memory():

            selection = (
                memory_list
                .curselection()
            )

            if not selection:

                messagebox.showinfo(
                    "Memory",
                    "Select a memory first.",
                    parent=window
                )

                return

            memory = displayed_memories[
                selection[0]
            ]

            edit_window = tk.Toplevel(
                window
            )

            edit_window.title(
                "Edit Memory"
            )

            edit_window.geometry(
                "600x420"
            )

            edit_window.configure(
                bg=self.panel
            )

            edit_window.transient(
                window
            )

            # ------------------------------------------------
            # Category
            # ------------------------------------------------

            tk.Label(
                edit_window,
                text="Category",
                font=(
                    "Segoe UI",
                    10,
                    "bold"
                ),
                fg=self.text,
                bg=self.panel
            ).pack(
                anchor="w",
                padx=20,
                pady=(20, 5)
            )

            category_entry = tk.Entry(
                edit_window,
                font=(
                    "Segoe UI",
                    10
                ),
                bg="#29292e",
                fg=self.text,
                insertbackground=self.text,
                relief="flat"
            )

            category_entry.pack(
                fill="x",
                padx=20,
                ipady=7
            )

            category_entry.insert(
                0,
                str(
                    memory.get(
                        "category",
                        "fact"
                    )
                )
            )

            # ------------------------------------------------
            # Importance
            # ------------------------------------------------

            tk.Label(
                edit_window,
                text="Importance",
                font=(
                    "Segoe UI",
                    10,
                    "bold"
                ),
                fg=self.text,
                bg=self.panel
            ).pack(
                anchor="w",
                padx=20,
                pady=(12, 5)
            )

            importance_entry = tk.Entry(
                edit_window,
                font=(
                    "Segoe UI",
                    10
                ),
                bg="#29292e",
                fg=self.text,
                insertbackground=self.text,
                relief="flat"
            )

            importance_entry.pack(
                fill="x",
                padx=20,
                ipady=7
            )

            importance_entry.insert(
                0,
                str(
                    memory.get(
                        "importance",
                        1.0
                    )
                )
            )

            # ------------------------------------------------
            # Content
            # ------------------------------------------------

            tk.Label(
                edit_window,
                text="Memory",
                font=(
                    "Segoe UI",
                    10,
                    "bold"
                ),
                fg=self.text,
                bg=self.panel
            ).pack(
                anchor="w",
                padx=20,
                pady=(12, 5)
            )

            content_text = tk.Text(
                edit_window,
                font=(
                    "Segoe UI",
                    10
                ),
                bg="#202025",
                fg=self.text,
                insertbackground=self.text,
                relief="flat",
                wrap="word"
            )

            content_text.pack(
                fill="both",
                expand=True,
                padx=20
            )

            content_text.insert(
                "1.0",
                str(
                    memory.get(
                        "content",
                        ""
                    )
                )
            )

            # ------------------------------------------------
            # Save
            # ------------------------------------------------

            def save_memory():

                content = (
                    content_text
                    .get(
                        "1.0",
                        "end-1c"
                    )
                    .strip()
                )

                category = (
                    category_entry
                    .get()
                    .strip()
                )

                importance_text = (
                    importance_entry
                    .get()
                    .strip()
                )

                if not content:

                    messagebox.showwarning(
                        "Memory",
                        "Memory content cannot be empty.",
                        parent=edit_window
                    )

                    return

                try:

                    importance = int(
                        importance_text
                    )

                except ValueError:

                    messagebox.showwarning(
                        "Memory",
                        "Importance must be a whole number from 1 to 10.",
                        parent=edit_window
                    )

                    return

                try:

                    updated = self.memory.edit_memory(
                        memory.get("id"),
                        category or "fact",
                        content,
                        importance
                    )

                    if not updated:
                        raise ValueError("Memory no longer exists.")

                except Exception as error:

                    messagebox.showerror(
                        "Memory Error",
                        str(error),
                        parent=edit_window
                    )

                    return

                edit_window.destroy()

                refresh_list()

                self.set_status(
                    "Memory updated.",
                    "ready"
                )

            tk.Button(
                edit_window,
                text="Save Memory",
                command=save_memory,
                font=(
                    "Segoe UI",
                    9,
                    "bold"
                ),
                fg="white",
                bg=self.accent,
                activebackground=self.accent_hover,
                activeforeground="white",
                relief="flat",
                borderwidth=0,
                padx=18,
                pady=8,
                cursor="hand2"
            ).pack(
                side="right",
                padx=20,
                pady=16
            )

        # ----------------------------------------------------
        # Delete
        # ----------------------------------------------------

        def delete_memory():

            selection = (
                memory_list
                .curselection()
            )

            if not selection:

                messagebox.showinfo(
                    "Memory",
                    "Select a memory first.",
                    parent=window
                )

                return

            memory = displayed_memories[
                selection[0]
            ]

            content = str(
                memory.get(
                    "content",
                    ""
                )
            )

            confirmed = messagebox.askyesno(
                "Delete Memory",
                (
                    "Delete this memory?\n\n"
                    + content
                ),
                parent=window
            )

            if not confirmed:

                return

            try:

                deleted = self.memory.delete_memory(
                    memory.get("id")
                )

                if not deleted:
                    raise ValueError("Memory no longer exists.")

                refresh_list()

                self.set_status(
                    "Memory deleted.",
                    "ready"
                )

            except ValueError:

                refresh_list()

            except Exception as error:

                messagebox.showerror(
                    "Memory Error",
                    str(error),
                    parent=window
                )

        # ----------------------------------------------------
        # Refresh when searching
        # ----------------------------------------------------

        search_entry.bind(
            "<KeyRelease>",
            lambda event:
            refresh_list()
        )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        buttons = tk.Frame(
            window,
            bg=self.panel,
            padx=20,
            pady=16
        )

        buttons.pack(
            fill="x"
        )

        tk.Button(
            buttons,
            text="Refresh",
            command=refresh_list,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            cursor="hand2"
        ).pack(
            side="left"
        )

        tk.Button(
            buttons,
            text="Edit Selected",
            command=edit_memory,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.text,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            cursor="hand2"
        ).pack(
            side="left",
            padx=8
        )

        tk.Button(
            buttons,
            text="Delete Selected",
            command=delete_memory,
            font=(
                "Segoe UI",
                9
            ),
            fg="#fca5a5",
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground="#fca5a5",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=7,
            cursor="hand2"
        ).pack(
            side="left"
        )

        tk.Button(
            buttons,
            text="Close",
            command=window.destroy,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.secondary,
            bg=self.panel_light,
            activebackground="#303035",
            activeforeground=self.text,
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=7,
            cursor="hand2"
        ).pack(
            side="right"
        )

        refresh_list()

        window.protocol(
            "WM_DELETE_WINDOW",
            window.destroy
        )


    # ========================================================
    # Conversation History
    # ========================================================

    def load_conversation_history(
        self
    ):

        if not self.conversation:

            return

        messages = getattr(
            self.conversation,
            "messages",
            []
        )

        if not messages:

            return

        loaded = 0

        for message in messages:

            if not isinstance(
                message,
                dict
            ):

                continue

            role = message.get(
                "role",
                ""
            )

            content = message.get(
                "content",
                ""
            )

            if not content:

                continue

            content = str(
                content
            ).strip()

            if not content:

                continue

            if role == "user":

                self.add_user_message(
                    content,
                    scroll=False
                )

                loaded += 1

            elif role == "assistant":

                self.add_assistant_message(
                    content,
                    scroll=False
                )

                loaded += 1

        if loaded:

            self.root.update_idletasks()

            self._update_scrollregion()

            self.chat_canvas.yview_moveto(
                1.0
            )

            self.root.after(
                100,
                self.scroll_to_bottom
            )

    # ========================================================
    # Backend ready
    # ========================================================

    def backend_ready(
        self
    ):

        name = self.character.get(
            "name",
            "Assistant"
        )

        self.character_label.configure(
            text=name
        )

        self.header_status.configure(
            text="● Online",
            fg=self.success
        )

        self.load_conversation_history()

        self.set_status(
            "Ready",
            "ready"
        )

        self.set_controls_enabled(
            True
        )

        self.add_system_message(
            f"{name} is ready."
        )

        self.root.after(
            100,
            self.scroll_to_bottom
        )

        self.input_entry.focus_set()

    # ========================================================
    # Backend error
    # ========================================================

    def backend_error(
        self,
        error
    ):

        self.header_status.configure(
            text="● Error",
            fg=self.listening
        )

        self.set_status(
            "Initialization failed",
            "error"
        )

        self.add_system_message(
            f"Startup error:\n{error}"
        )

    # ========================================================
    # Controls
    # ========================================================

    def set_controls_enabled(
        self,
        enabled
    ):

        state = (
            "normal"
            if enabled
            else "disabled"
        )

        self.input_entry.configure(
            state=state
        )

        self.send_button.configure(
            state=state
        )

    # ========================================================
    # Status
    # ========================================================

    def set_status(
        self,
        text,
        state="normal"
    ):

        colors = {
            "normal": self.secondary,
            "ready": self.success,
            "listening": self.listening,
            "thinking": self.thinking,
            "speaking": self.accent,
            "error": self.listening
        }

        color = colors.get(
            state,
            self.secondary
        )

        self.status_label.configure(
            text=text,
            fg=color
        )

        self.status_dot.configure(
            fg=color
        )

        if state == "listening":

            self.ptt_indicator.configure(
                text="🔴",
                fg=self.listening
            )

        elif state == "thinking":

            self.ptt_indicator.configure(
                text="🧠",
                fg=self.thinking
            )

        elif state == "speaking":

            self.ptt_indicator.configure(
                text="🔊",
                fg=self.accent
            )

        elif state == "error":

            self.ptt_indicator.configure(
                text="⚠",
                fg=self.listening
            )

        else:

            self.ptt_indicator.configure(
                text="🎙",
                fg=self.secondary
            )

    # ========================================================
    # Chat bubbles
    # ========================================================

    def create_avatar_widget(
        self,
        parent
    ):

        if (
            not self.avatar_path
            or not PIL_AVAILABLE
        ):

            return tk.Label(
                parent,
                text="●",
                font=(
                    "Segoe UI",
                    18
                ),
                fg=self.accent,
                bg=self.bg
            )

        try:

            image = Image.open(
                self.avatar_path
            ).convert(
                "RGBA"
            )

            image = image.resize(
                (
                    42,
                    42
                ),
                Image.Resampling.LANCZOS
            )

            mask = Image.new(
                "L",
                (
                    42,
                    42
                ),
                0
            )

            draw = ImageDraw.Draw(
                mask
            )

            draw.ellipse(
                (
                    0,
                    0,
                    41,
                    41
                ),
                fill=255
            )

            image.putalpha(
                mask
            )

            photo = ImageTk.PhotoImage(
                image
            )

            label = tk.Label(
                parent,
                image=photo,
                bg=self.bg
            )

            label.image = photo

            return label

        except Exception:

            return tk.Label(
                parent,
                text="●",
                font=(
                    "Segoe UI",
                    18
                ),
                fg=self.accent,
                bg=self.bg
            )

    def add_bubble(
        self,
        speaker,
        text,
        is_user=False,
        scroll=True
    ):

        row = tk.Frame(
            self.chat_frame,
            bg=self.bg
        )

        row.pack(
            fill="x",
            padx=12,
            pady=7
        )

        if is_user:

            bubble = tk.Frame(
                row,
                bg=self.user_bubble
            )

            bubble.pack(
                anchor="e",
                padx=4
            )

            name = tk.Label(
                bubble,
                text=speaker,
                font=(
                    "Segoe UI",
                    9,
                    "bold"
                ),
                fg="#c4b5fd",
                bg=self.user_bubble
            )

            name.pack(
                anchor="w",
                padx=14,
                pady=(9, 2)
            )

            message = tk.Label(
                bubble,
                text=text,
                font=(
                    "Segoe UI",
                    11
                ),
                fg=self.text,
                bg=self.user_bubble,
                justify="left",
                anchor="w",
                wraplength=620
            )

            message.pack(
                anchor="w",
                padx=14,
                pady=(0, 11)
            )

        else:

            content = tk.Frame(
                row,
                bg=self.bg
            )

            content.pack(
                anchor="w",
                padx=4
            )

            avatar = self.create_avatar_widget(
                content
            )

            avatar.pack(
                side="left",
                anchor="n",
                padx=(0, 10),
                pady=3
            )

            bubble = tk.Frame(
                content,
                bg=self.assistant_bubble
            )

            bubble.pack(
                side="left"
            )

            name = tk.Label(
                bubble,
                text=speaker,
                font=(
                    "Segoe UI",
                    9,
                    "bold"
                ),
                fg=self.accent,
                bg=self.assistant_bubble
            )

            name.pack(
                anchor="w",
                padx=14,
                pady=(9, 2)
            )

            message = tk.Label(
                bubble,
                text=text,
                font=(
                    "Segoe UI",
                    11
                ),
                fg=self.text,
                bg=self.assistant_bubble,
                justify="left",
                anchor="w",
                wraplength=620
            )

            message.pack(
                anchor="w",
                padx=14,
                pady=(0, 11)
            )

        if scroll:

            self.root.after(
                10,
                self.scroll_to_bottom
            )

    # ========================================================
    # Messages
    # ========================================================

    def add_user_message(
        self,
        text,
        scroll=True
    ):

        self.add_bubble(
            "You",
            text,
            True,
            scroll
        )

    def add_assistant_message(
        self,
        text,
        scroll=True
    ):

        name = self.character.get(
            "name",
            "Assistant"
        )

        self.add_bubble(
            name,
            text,
            False,
            scroll
        )

    def add_system_message(
        self,
        text
    ):

        row = tk.Frame(
            self.chat_frame,
            bg=self.bg
        )

        row.pack(
            fill="x",
            padx=20,
            pady=6
        )

        label = tk.Label(
            row,
            text=text,
            font=(
                "Segoe UI",
                9
            ),
            fg=self.muted,
            bg=self.bg,
            justify="left",
            wraplength=700
        )

        label.pack(
            anchor="center"
        )

        self.root.after(
            10,
            self.scroll_to_bottom
        )

    # ========================================================
    # Backend events
    # ========================================================

    def handle_backend_event(
        self,
        event
    ):

        if self.closed:

            return

        try:

            self.root.after(
                0,
                lambda event=event:
                self.handle_backend_event_ui(
                    event
                )
            )

        except tk.TclError:

            pass

    def handle_backend_event_ui(
        self,
        event
    ):

        if self.closed:

            return

        if event.type == "status":

            self.set_status(
                event.data.get(
                    "message",
                    ""
                ),
                event.data.get(
                    "state",
                    "normal"
                )
            )

        elif event.type == "assistant_response":

            self.add_assistant_message(
                event.data.get(
                    "content",
                    ""
                )
            )

        elif event.type == "voice_state":

            self.handle_ptt_state_ui(
                event.data.get(
                    "state",
                    "ready"
                )
            )

        elif event.type == "voice_transcription":

            content = event.data.get("content")

            if content:

                self.add_user_message(
                    content
                )

                self.set_status(
                    "Transcribed · Thinking...",
                    "thinking"
                )

        elif event.type == "error":

            source = event.data.get(
                "source"
            )

            prefix = (
                "TTS error"
                if source == "tts"
                else "Error"
            )

            self.add_system_message(
                f"{prefix}: "
                f"{event.data.get('message', '')}"
            )

    # ========================================================
    # Typed Message
    # ========================================================

    def send_message(
        self,
        event=None
    ):

        if self.processing:

            return "break"

        text = (
            self.input_entry
            .get()
            .strip()
        )

        if not text:

            return "break"

        self.input_entry.delete(
            0,
            "end"
        )

        self.start_turn(
            text
        )

        return "break"

    # ========================================================
    # Start Turn
    # ========================================================

    def start_turn(
        self,
        user_message
    ):

        if not user_message:

            return

        if not self.processing_lock.acquire(
            blocking=False
        ):

            self.set_status(
                "Still processing...",
                "thinking"
            )

            return

        self.processing = True

        self.add_user_message(
            user_message
        )

        self.set_status(
            "Thinking...",
            "thinking"
        )

        self.set_controls_enabled(
            False
        )

        threading.Thread(
            target=self.process_turn,
            args=(
                user_message,
            ),
            daemon=True
        ).start()

    # ========================================================
    # Process Turn
    # ========================================================

    def process_turn(
        self,
        user_message
    ):

        try:

            self.service.process_text_turn(
                user_message
            )

        except Exception as error:

            if not self.closed:

                self.root.after(
                    0,
                    lambda error=error:
                    self.add_system_message(
                        f"Error: {error}"
                    )
                )

        finally:

            self.processing = False

            try:

                self.processing_lock.release()

            except RuntimeError:

                pass

            if not self.closed:

                self.root.after(
                    0,
                    self.turn_finished
                )

    # ========================================================
    # Turn Finished
    # ========================================================

    def turn_finished(
        self
    ):

        self.set_status(
            "Ready",
            "ready"
        )

        self.set_controls_enabled(
            True
        )

        self.input_entry.focus_set()

        self.root.after(
            50,
            self.scroll_to_bottom
        )

    # ========================================================
    # PTT State
    # ========================================================

    def handle_ptt_state(
        self,
        state
    ):

        if self.closed:

            return

        self.root.after(
            0,
            lambda state=state:
            self.handle_ptt_state_ui(
                state
            )
        )

    def handle_ptt_state_ui(
        self,
        state
    ):

        if state == "listening":

            self.set_status(
                "Listening...",
                "listening"
            )

        elif state == "released":

            self.set_status(
                "Transcribing...",
                "thinking"
            )

        elif state == "ready":

            if not self.processing:

                self.set_status(
                    "Ready",
                    "ready"
                )

    # ========================================================
    # PTT transcription
    # ========================================================

    def handle_ptt_transcription(
        self,
        text
    ):

        if not text:

            return

        self.root.after(
            0,
            lambda text=text:
            self.handle_ptt_on_ui(
                text
            )
        )

    def handle_ptt_on_ui(
        self,
        text
    ):

        if self.closed:

            return

        if self.processing:

            self.set_status(
                "Still processing...",
                "thinking"
            )

            return

        self.set_status(
            "Transcribed • Thinking...",
            "thinking"
        )

        self.start_turn(
            text
        )

    # ========================================================
    # Volume
    # ========================================================

    def change_volume(
        self,
        value
    ):

        if not self.service:

            return

        try:

            volume = (
                float(value)
                / 100.0
            )

            self.service.set_tts_volume(
                volume
            )

            self.volume_value.configure(
                text=f"{int(float(value))}%"
            )

        except Exception:

            pass

    # ========================================================
    # Stop Speaking
    # ========================================================

    def stop_speaking(
        self
    ):

        if self.service:

            try:

                self.service.stop_speaking()

            except Exception:

                pass

        if not self.processing:

            self.set_status(
                "Ready",
                "ready"
            )

    # ========================================================
    # Close
    # ========================================================

    def close(
        self
    ):

        if self.closed:

            return

        self.closed = True

        try:

            if self.service:

                self.service.close()

            elif self.ptt:

                self.ptt.stop()

            elif self.tts:

                self.tts.stop()

        except Exception as error:

            print(
                f"Shutdown error: {error}"
            )

        try:

            self.chat_canvas.unbind_all(
                "<MouseWheel>"
            )

        except Exception:

            pass

        self.root.destroy()


# ============================================================
# Entry Point
# ============================================================

def main():

    root = tk.Tk()

    AIFrenGUI(
        root
    )

    root.mainloop()


if __name__ == "__main__":

    main()
