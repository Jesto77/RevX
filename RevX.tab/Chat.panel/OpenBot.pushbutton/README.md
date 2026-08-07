# RevitBot - Your AI-Powered Revit Assistant

A pyRevit extension that provides a live popup chat bot inside Revit. Chat with RevitBot to execute Revit commands, export files, create elements, modify parameters, and more!

---

## Features

### Chat Interface
- Live popup window inside Revit (non-modal, you can still use Revit while chatting)
- Dark themed modern UI with color-coded messages
- Quick tool buttons for common operations
- Natural language command parsing
- Typing indicator while processing

### Available Commands

#### Export Operations
| Command | Description |
|---------|-------------|
| `export nwc [path]` | Export Navisworks (.nwc) file |
| `export ifc [path]` | Export IFC file |
| `export dwg [path]` | Export AutoCAD DWG file |
| `export pdf [path]` | Export/Print to PDF |

#### File Operations
| Command | Description |
|---------|-------------|
| `save` | Save current file |
| `save as [path]` | Save file to specific location |
| `save to C:\Projects\model.rvt` | Save to a folder |

#### Create Operations
| Command | Description |
|---------|-------------|
| `create filled region with pick lines` | Create filled region by picking lines |
| `create filled region` | Create rectangular filled region |
| `create sheet [number] [name]` | Create a new sheet |
| `create room [level] [name]` | Create a room |
| `create section` | Create a section view |
| `create callout` | Create a callout view |
| `create level [elevation] [name]` | Create a level |
| `create grid [x1 y1 x2 y2]` | Create a grid line |
| `create dimension` | Create a dimension (pick references) |
| `create text [content]` | Create a text note |
| `create detail line [x1 y1 x2 y2]` | Create a detail line |
| `create room tags` | Tag all rooms |
| `create workset [name]` | Create a workset |
| `create filter [name]` | Create a view filter |
| `create detail component [name]` | Place a detail component |

#### Modify Operations
| Command | Description |
|---------|-------------|
| `set parameter [name] to [value]` | Set parameter on selected elements |
| `get parameter [name]` | Get parameter from selected elements |
| `batch set [param] of [category] to [value]` | Bulk set parameter |
| `delete selected` | Delete selected elements |
| `select all [category]` | Select all elements of category |

#### List/Query Operations
| Command | Description |
|---------|-------------|
| `list views` | List all views |
| `list sheets` | List all sheets |
| `list levels` | List all levels |
| `list families [category]` | List families |
| `list worksets` | List worksets |
| `list materials` | List materials |
| `list categories` | List categories |
| `list [category]` | List elements of a category |
| `doc info` / `project info` | Show project information |
| `list filled region types` | Show filled region types |

#### Fun Stuff
| Command | Description |
|---------|-------------|
| `help` | Show all available commands |
| `tell me a joke` | Get a Revit joke |
| `give me a tip` | Get a pro tip |
| `hello` / `hi` | Greet the bot |
| `thanks` | Thank the bot |

---

## Installation

### Prerequisites
- **pyRevit** installed (get it from [pyrevitlabs.notion.site](https://pyrevitlabs.notion.site))
- **Revit** 2018 or later

### Step-by-Step Install

1. **Copy the extension folder**
   Copy the entire `RevitBot.extension` folder to your pyRevit extensions directory:
   ```
   %appdata%\pyRevit\Extensions\
   ```
   The full path should be:
   ```
   %appdata%\pyRevit\Extensions\RevitBot.extension\
   ```

2. **Reload pyRevit**
   In Revit, click the pyRevit tab → "Reload" button (or restart Revit).

3. **Launch RevitBot**
   You'll see a new "RevitBot" tab in Revit with a "Chat" panel and "Open RevitBot" button. Click it!

### Alternative: Manual Install via pyRevit Settings

1. Open pyRevit settings (pyRevit tab → Settings)
2. Go to the "Extensions" section
3. Add the path to the `RevitBot.extension` folder
4. Click "Save & Reload"

---

## Usage Examples

### Exporting NWC
```
User: export nwc
Bot: NWC exported successfully to: C:\Projects\model.nwc

User: export nwc to C:\Output\myproject.nwc  
Bot: NWC exported successfully to: C:\Output\myproject.nwc
```

### Saving Files
```
User: save file to C:\Backup\project_backup.rvt
Bot: File saved to: C:\Backup\project_backup.rvt

User: save
Bot: File saved successfully.
```

### Creating Filled Regions
```
User: create filled region with pick lines
Bot: I'll start the pick lines mode for creating a filled region.
     The chat window will hide temporarily while you pick lines.
     Click the lines you want to use as the boundary, then press ESC or Finish.
     >>> Starting pick mode... Please switch to Revit and pick your lines! <<<

User: create filled region
Bot: Rectangular filled region created!
     Position: (0, 0)
     Size: 10 x 10
```

### Setting Parameters
```
User: set parameter Comments to Approved
Bot: Set 'Comments' on element 123456
     Set 'Comments' on element 234567

User: batch set Comments of Doors to Approved
Bot: Set 'Comments' = 'Approved' on 45 elements of 'Doors'.
```

### Getting Project Info
```
User: doc info
Bot: Document Information:
     Title: MyProject
     Path: C:\Projects\MyProject.rvt
     Active View: Floor Plan - Level 1
     Total Elements: 3452
     Total Views: 24
     Total Sheets: 8
     Levels: Level 1, Level 2, Level 3
```

---

## Project Structure

```
RevitBot.extension/
├── extension.yaml                          # Extension metadata
└── RevitBot.tab/
    └── Chat.panel/
        └── OpenBot.pushbutton/
            ├── bundle.yaml                  # Button metadata
            ├── script.py                    # Main script - launches the bot window
            ├── bot_window.xaml              # XAML layout reference
            ├── revit_tools.py               # All Revit API tool functions
            ├── chat_engine.py               # NLP command parser & router
            └── README.md                    # This file
```

---

## Architecture

### script.py
- Creates the WPF chat window (built manually for IronPython compatibility)
- Handles UI events (send, quick tools, close)
- Manages message display (bot, user, tool, error messages)
- Non-modal window allows simultaneous Revit interaction

### revit_tools.py
- Contains all Revit API tool functions (30+ tools)
- Each tool returns (success, message) tuple
- Handles Transactions properly for all modifications
- Supports interactive pick operations (lines, references)

### chat_engine.py
- Natural language command parser using regex pattern matching
- Routes commands to appropriate tool functions
- Extracts parameters (paths, names, values) from messages
- Provides fallback responses and helpful suggestions
- Includes jokes, tips, and social interactions

---

## Troubleshooting

### "RevitBot" tab doesn't appear
- Make sure pyRevit is installed and loaded
- Check the extension is in the correct folder
- Try reloading pyRevit or restarting Revit

### Bot window doesn't open
- Check the Revit output window for error messages
- Ensure you have an active document open
- Try running the script from pyRevit's script editor for detailed errors

### Command not recognized
- Type `help` to see all available commands
- Commands are flexible - try different phrasings:
  - "export nwc" or "nwc export" or "create nwc file"
  - "create sheet" or "new sheet" or "add sheet"

### Pick operations (filled region, dimension) not working
- The chat window needs to be minimized during pick operations
- Make sure you're in the correct view before starting
- Pick operations require an active Revit view

---

## Extending RevitBot

### Adding New Tools

1. Add a new method in `revit_tools.py`:
```python
def my_new_tool(self, param=None):
    """My new tool description."""
    try:
        # ... Revit API code ...
        return True, "Tool executed successfully!"
    except Exception as ex:
        return False, "Error: {}".format(str(ex))
```

2. Add command patterns in `chat_engine.py`:
```python
{
    "name": "my_new_command",
    "patterns": [
        r"(?:create|make)\s+my\s+tool",
    ],
    "handler": self._handle_my_new_command,
    "description": "My new tool",
    "usage": "create my tool [param]",
    "examples": ["create my tool"]
},
```

3. Add the handler method:
```python
def _handle_my_new_command(self, message):
    success, msg = self.tools.my_new_tool()
    return msg, "tool" if success else "error"
```

4. Optionally add a quick tool button in `script.py`.

---

## License

Free to use, modify, and distribute. Made for the Revit community!

---

## Credits

Built with:
- [pyRevit](https://github.com/eirannejad/pyRevit) - Python scripting for Revit
- Revit API - Autodesk Revit .NET API
- WPF - Windows Presentation Foundation for the UI
- IronPython - Python runtime for .NET
