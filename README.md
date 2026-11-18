# hearing_bot

Push-to-talk voice typing for Windows. Hold Right Alt, say something, let go, and your words are typed wherever the cursor is. The same key can also translate your speech to English, save voice notes, and fix, rewrite or explain selected text with AI.

Speech recognition runs on [Groq](https://groq.com)'s hosted Whisper models, so the bot itself stays small: it uses about 30 MB of RAM and runs hidden in the background.

## What you need

- Windows 10 or 11
- [Python 3.10 or newer](https://www.python.org/downloads/)
- A microphone
- A free Groq API key from [console.groq.com/keys](https://console.groq.com/keys)

## Install

```powershell
git clone https://github.com/Priyanshukv06/hearing_bot.git
cd hearing_bot
.\install.cmd
```

You can also download the ZIP from GitHub, unzip it and double-click `install.cmd`.

The installer sets up a Python virtual environment, asks for your Groq API key, makes the bot start every time you sign in, adds the `git aic` command and starts the bot. It doesn't need admin rights. The key is saved as the `GROQ_API_KEY` environment variable for your Windows user.

## Shortcuts

Everything uses **Right Alt**. For the voice shortcuts, press Right Alt, tap the letter, then keep holding Right Alt while you talk.

| Keys | What happens |
|---|---|
| Hold Right Alt and talk | Types what you say |
| Right Alt + **T**, then talk | Types the English translation of what you say, from any language |
| Right Alt + **N**, then talk | Adds what you say to `voice_notes.md` in your Documents folder, with the date and time |
| Right Alt + **R**, then talk | Rewrites the selected text the way you ask, e.g. "make it formal" or "make it shorter" |
| Right Alt + **F** | Fixes the spelling and grammar of the selected text |
| Right Alt + **E** | Explains the selected text (an error, some code, anything) in a popup |

While it records, the Windows microphone icon shows in the taskbar. An error beep means something went wrong, and the reason is written to `hearing_bot.log`.

### AI commit messages

In any git repository, stage your changes and run:

```powershell
git aic
```

It writes a commit message from your staged changes and asks whether to commit, edit the message first, or cancel.

## Settings

The settings are at the top of `hearing_bot.py`:

- `HOTKEY_VK` is the key you hold. For example, `0xA3` is Right Ctrl and `0x14` is Caps Lock.
- `LANGUAGE` is the language you dictate in. `None` lets Whisper detect it.
- `ACTION_KEYS` sets the letter for each shortcut.

The Groq models are set at the top of `groq_api.py`. After changing anything, run `install.cmd` again to restart the bot.

## Good to know

- **AltGr keyboards:** on layouts where Right Alt is AltGr (for example German, French, Polish or India), you can't type AltGr characters with Right Alt while the bot runs. Ctrl + Left Alt + the key still types them. You can also change `HOTKEY_VK`.
- **Terminals:** Fix, Rewrite and Explain copy your selection with Ctrl+C. In a terminal, select the text first, because Ctrl+C with nothing selected stops the running command.
- **Clipboard:** those three shortcuts put the text you had on the clipboard back afterwards. An image or file on the clipboard is not put back.
- **Privacy:** your recordings and the text you select are sent to Groq for processing. The log only records timings and errors, never what you said.
- **Free plan limits:** Groq's free plan allows about 2,000 speech requests a day.
- **Nothing gets typed?** Check that Settings > Privacy & security > Microphone > "Let desktop apps access your microphone" is on, then look in `hearing_bot.log`.

## Uninstall

Double-click `uninstall.cmd`, then delete the folder.

## License

[MIT](LICENSE)
