# nemlig-cli

Command-line client for [nemlig.com](https://www.nemlig.com), the Danish online grocery store.
Search products, manage your basket, browse order history and keep a local grocery list,
all from the terminal.

Single-file Python implementation (`nemlig_cli.py`) that talks directly to nemlig.com's
web API via `requests`. Originally based on [eisbaw/nemlig_cli](https://github.com/eisbaw/nemlig_cli).

## Install

Requires Python >= 3.11. With [uv](https://docs.astral.sh/uv/):

```bash
# Run without installing
uvx --from git+https://github.com/simontjell/nemlig-cli nemlig search "mælk"

# Or install as a tool
uv tool install git+https://github.com/simontjell/nemlig-cli
nemlig search "mælk"
```

Or with pip:

```bash
pip install git+https://github.com/simontjell/nemlig-cli
```

To use it as a dependency in another `uv` project:

```toml
[project]
dependencies = ["nemlig-cli"]

[tool.uv.sources]
nemlig-cli = { git = "https://github.com/simontjell/nemlig-cli" }
```

## Credentials

You need a nemlig.com account. Credentials are looked up in this order:

1. CLI flags: `-u EMAIL -p PASSWORD`
2. `~/.config/nemlig/login.json`: `{"username": "you@example.com", "password": "secret"}`
3. `.env` in the current directory (see `.env.example`)
4. Environment variables `NEMLIG_USER` and `NEMLIG_PASS`

## Usage

```bash
nemlig search "cocio"             # search products
nemlig details 701025             # full product info (ingredients, allergens, price)
nemlig basket                     # show current nemlig basket
nemlig add 701025 --quantity 2    # add product to basket
nemlig history                    # list past orders
nemlig history 12345678           # line items of one order
nemlig refresh-history [-l 50]    # cache recent orders with line items to
                                  # ~/.config/nemlig/historik_cache.json
```

### Local grocery list

A local list, stored in `~/.config/nemlig/grocery_list.json`, that can be pushed to the
nemlig basket in one go:

```bash
nemlig list                       # show list
nemlig list add "mælk"            # search and pick a product to add
nemlig list remove 701025
nemlig list clear
nemlig list sync                  # push all items to the nemlig basket
```

### Interactive mode

Run `nemlig` without arguments for a REPL with tab completion.

### Shell completion

The script is `argcomplete`-enabled:

```bash
eval "$(register-python-argcomplete nemlig)"
```

## Optional extras

```bash
uv tool install "git+https://github.com/simontjell/nemlig-cli[all]"
```

- `ai`: Anthropic API integration
- `forms`: Google Sheets import
- `scanner`: barcode scanning via webcam (pyzbar, OpenCV, Open Food Facts)

## License

MIT
