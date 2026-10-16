"""赛博情报局 — 主入口"""

from pathlib import Path

from src.fetcher import fetch_all, format_as_markdown
from src.source_manager import interactive_add, interactive_list, list_sources

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"


def run_briefing():
    """执行抓取并生成简报。"""
    sources = list_sources(following_only=True)
    if not sources:
        print("\n你还没有关注任何人，先添加关注再抓取吧！")
        return

    articles = fetch_all()
    if not articles:
        print("\n没有抓到内容。")
        return

    report = format_as_markdown(articles)

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    output_file = OUTPUT_DIR / f"{today}.md"
    output_file.write_text(report, encoding="utf-8")

    print(f"\n简报已保存到：{output_file}")


def main():
    while True:
        print("\n" + "=" * 40)
        print("  赛博情报局")
        print("=" * 40)
        print("  1. 添加关注")
        print("  2. 查看关注列表")
        print("  3. 抓取并生成简报")
        print("  4. 退出")
        print("=" * 40)

        choice = input("\n请选择操作：").strip()

        if choice == "1":
            interactive_add()
        elif choice == "2":
            interactive_list()
        elif choice == "3":
            run_briefing()
        elif choice == "4":
            print("再见！")
            break
        else:
            print("无效输入，请输入 1-4。")


if __name__ == "__main__":
    main()
