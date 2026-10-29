# Install Sesta Workbench

[English introduction](../README.md) · [繁體中文介紹](../README.zh-TW.md)

The repository is a plugin marketplace for Codex and Claude Code. Both install the same inspection skill and bundle the Python workbench. Use a current host release with plugin marketplace support, Python 3.10 or newer, and the host's browser or computer-use tools for visual inspection.

## Codex

Run in your terminal:

```sh
codex plugin marketplace add chaosbeagle/sixth-magnitude
codex plugin add sesta@sixth-magnitude
```

In Codex desktop, you can also add `https://github.com/chaosbeagle/sixth-magnitude` through the plugin marketplace controls, then install Sesta Workbench. Start a new chat after installation. Ask Codex to use Sesta Workbench, or select its `inspect` skill from the skill picker.

## Claude Code

Run in your terminal:

```sh
claude plugin marketplace add chaosbeagle/sixth-magnitude
claude plugin install sesta@sixth-magnitude
```

Start a new Claude Code session. Invoke `/sesta:inspect`, followed by your task. A natural-language request to use Sesta Workbench also lets the host select the skill.

## First workspace

After installing, ask the agent:

> Use Sesta Workbench to set up a synthetic inspection demo in this working directory. Create a Python environment here, install the bundled workbench, start the local viewer, and open it. Show me how to select a region and verify a saved observation.

The skill guides the agent to install the workbench from the bundled plugin source into an environment in your working directory. Installation of the plugin itself does not install Python dependencies or start a server. The first workbench installation needs access to a Python package index unless dependencies are already available. Keep runs and environments outside the plugin cache so updates do not replace your work.

For manual setup, clone this repository and follow [Try the workbench](../README.md#try-the-workbench). Native WSI readers are optional; see the [pathology guide](pathology.md).

Choose your model and reasoning effort in the host. The plugin adds no model API key, automatic hooks, or separate MCP server. It uses the host's existing tools. If visual browser access is unavailable, enable a supported computer-use integration or inspect the viewer yourself; saving a region through code alone does not count as a visual check. Images shown to a cloud model are processed through that host's model service.

## Local development and checks

From the repository root:

```sh
python scripts/check_plugin.py
claude plugin validate --strict .claude-plugin/plugin.json
claude plugin validate --strict .claude-plugin/marketplace.json
```

Both marketplace commands also accept an absolute path to a local checkout. Claude Code can load the checkout for one session with `claude --plugin-dir /absolute/path/to/sixth-magnitude`. Keep the plugin skill and its Python-package copy identical; the check above catches differences.

The portable root manifest is accompanied by a Codex compatibility manifest and a Claude Code manifest. The two marketplace catalogs point to the repository root, so installed plugins include the workbench, documentation and licenses. These files follow the [OpenAI packaging specification](https://developers.openai.com/plugins/build/plugins), [Claude Code manifest reference](https://code.claude.com/docs/en/plugins-reference) and [marketplace guide](https://code.claude.com/docs/en/plugin-marketplaces).
