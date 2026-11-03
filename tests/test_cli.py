"""CLI-level: index/ask/roots/list/forget through main(), exit-code honesty."""

from __future__ import annotations

from pathlib import Path

from mddex.__main__ import main


def test_full_cli_flow(tmp_path, capsys):
    tree = tmp_path / "docs"
    tree.mkdir()
    (tree / "alpha.md").write_text("# Alpha Config\n")
    (tree / "beta.md").write_text("# Beta Release\n")
    db = ["--db", str(tmp_path / "docs.db")]

    assert main(db + ["index", str(tree)]) == 0
    assert "indexed 2 docs" in capsys.readouterr().out

    assert main(db + ["ask", "alpha config"]) == 0
    out = capsys.readouterr().out
    assert "alpha.md" in out

    assert main(db + ["roots"]) == 0
    assert "2" in capsys.readouterr().out

    assert main(db + ["list"]) == 0
    assert "Alpha Config" in capsys.readouterr().out

    assert main(db + ["forget", str(tree)]) == 0
    assert "2 docs removed" in capsys.readouterr().out


def test_ask_without_index_is_clean_error(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nothing.db"), "ask", "anything"]) == 1
    err = capsys.readouterr().err
    assert "mddex index" in err


def test_ask_unknown_root_is_clean_error(tmp_path, capsys):
    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "x.md").write_text("# X\n")
    db = ["--db", str(tmp_path / "docs.db")]
    main(db + ["index", str(tree)])
    capsys.readouterr()
    other = tmp_path / "other"
    other.mkdir()
    assert main(db + ["ask", "x", "--root", str(other)]) == 1
    assert "not indexed" in capsys.readouterr().err


def test_index_bad_dir_is_clean_error(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "d.db"), "index", str(tmp_path / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_version_flag(capsys):
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    assert "mddex" in capsys.readouterr().out
