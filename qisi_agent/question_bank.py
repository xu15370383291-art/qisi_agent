from __future__ import annotations

"""Build and persist a deterministic question bank from the PostgreSQL corpus."""

import hashlib
import json
import re
from typing import Any

import psycopg


PROMPTS = (
    "下列关于“{point}”的说法，正确的是？",
    "下列结论中，符合“{point}”定义或性质的是？",
    "小明复习“{point}”时作出如下判断，其中正确的是？",
    "应用“{point}”时，首先应关注下列哪一项？",
    "下列关于“{point}”的说法，错误的是？",
)


def _clean_sentence(content: str, point: str) -> str:
    text = re.sub(r"知识点[:：][^\n]+", "", content or "")
    text = re.sub(r"\s+", "", text).strip("。；; \n")
    if not text:
        return f"掌握“{point}”需要结合本节教材中的定义、性质和使用条件。"
    # Keep generated options readable in the card and avoid copying a whole section.
    sentence = re.split(r"(?<=[。！？；])", text)[0].strip("。；; ")
    return (sentence or text)[:115] + ("。" if not (sentence or text).endswith(("。", "！", "？")) else "")


def _question_id(grade_id: str, point: str, variant: int) -> str:
    digest = hashlib.sha1(f"{grade_id}\0{point}\0{variant}".encode("utf-8")).hexdigest()[:20]
    return f"bank_{digest}"


def _special_questions(point: str) -> list[dict[str, Any]] | None:
    """Hand-authored numerical variants for high-frequency calculation points."""
    specs: dict[str, list[dict[str, Any]]] = {
        "分数约分": [
            {"prompt": "将 84/126 约分为最简分数，结果是？", "options": ["2/3", "3/4", "4/5", "6/7"], "answer_index": 0, "difficulty": "进阶", "explanation": "84 和 126 的最大公因数是 42，同时除以 42 得 2/3。"},
            {"prompt": "下列分数中，已经是最简分数的是？", "options": ["12/18", "15/25", "14/21", "7/16"], "answer_index": 3, "difficulty": "基础", "explanation": "7 与 16 互质，因此 7/16 已经是最简分数。"},
            {"prompt": "若 18/24 = a/4，则 a 的值是？", "options": ["2", "3", "4", "6"], "answer_index": 1, "difficulty": "综合", "explanation": "18/24 约分为 3/4，所以 a=3。"},
            {"prompt": "把 5/6 的分子、分母同时乘以 4，所得分数与 5/6 的关系是？", "options": ["变大", "变小", "相等", "无法判断"], "answer_index": 2, "difficulty": "易错", "explanation": "分子和分母同时乘以同一个非零数，分数的值不变。"},
            {"prompt": "一块蛋糕的 12/18 被吃掉，约分后表示吃掉了整块蛋糕的？", "options": ["1/2", "2/3", "3/4", "4/5"], "answer_index": 1, "difficulty": "应用", "explanation": "12/18 同除以最大公因数 6，得到 2/3。"},
        ],
        "一元一次方程": [
            {"prompt": "解方程：3x-7=11。", "options": ["4", "6", "8", "9"], "answer_index": 1, "difficulty": "基础", "explanation": "移项得 3x=18，所以 x=6。"},
            {"prompt": "解方程：2(3x-1)=4x+6。", "options": ["2", "3", "4", "5"], "answer_index": 2, "difficulty": "进阶", "explanation": "去括号得 6x-2=4x+6，解得 x=4。"},
            {"prompt": "解方程：x/3+2=5。", "options": ["6", "7", "9", "12"], "answer_index": 2, "difficulty": "进阶", "explanation": "两边减 2 得 x/3=3，所以 x=9。"},
            {"prompt": "某数的 3 倍比它大 18，这个数是？", "options": ["6", "9", "12", "18"], "answer_index": 1, "difficulty": "应用", "explanation": "设这个数为 x，则 3x=x+18，解得 x=9。"},
            {"prompt": "若关于 x 的方程 2x+a=10 的解为 x=3，则 a 的值是？", "options": ["2", "3", "4", "6"], "answer_index": 2, "difficulty": "综合", "explanation": "将 x=3 代入得 6+a=10，所以 a=4。"},
        ],
        "绝对值": [
            {"prompt": "|-7| 的值是？", "options": ["-7", "0", "7", "14"], "answer_index": 2, "difficulty": "基础", "explanation": "绝对值表示到原点的距离，因此 |-7|=7。"},
            {"prompt": "若 |x-3|=5，则 x 的值为？", "options": ["8", "-2", "8 或 -2", "2 或 -8"], "answer_index": 2, "difficulty": "综合", "explanation": "x-3=5 或 x-3=-5，所以 x=8 或 x=-2。"},
            {"prompt": "下列说法正确的是？", "options": ["|a| 一定大于 a", "|a| 一定是正数", "|a|≥0", "|a|=a 只在 a<0 时成立"], "answer_index": 2, "difficulty": "定义", "explanation": "绝对值具有非负性，|a|≥0。"},
            {"prompt": "数轴上表示 -4 的点到原点的距离是？", "options": ["-4", "0", "4", "8"], "answer_index": 2, "difficulty": "基础", "explanation": "距离不能为负，|-4|=4。"},
            {"prompt": "若 |a|=|b|，则 a 与 b 的关系可能是？", "options": ["a=b", "a=-b", "a=b 或 a=-b", "a>b"], "answer_index": 2, "difficulty": "易错", "explanation": "两个数绝对值相等，可能相等，也可能互为相反数。"},
        ],
        "平方根": [
            {"prompt": "√49 的值是？", "options": ["-7", "-1", "7", "49"], "answer_index": 2, "difficulty": "基础", "explanation": "算术平方根取非负值，√49=7。"},
            {"prompt": "若 √(x+5)=3，则 x 的值是？", "options": ["2", "3", "4", "5"], "answer_index": 3, "difficulty": "进阶", "explanation": "两边平方得 x+5=9，所以 x=4。"},
            {"prompt": "下列说法正确的是？", "options": ["√(-4)= -2", "√a 一定有意义", "√a 有意义的条件是 a≥0", "√a 的值可以为负数"], "answer_index": 2, "difficulty": "定义", "explanation": "实数范围内二次根式有意义的条件是被开方数不小于 0。"},
            {"prompt": "若一个正方形的面积为 81 cm²，则它的边长是？", "options": ["8 cm", "9 cm", "18 cm", "40.5 cm"], "answer_index": 1, "difficulty": "应用", "explanation": "正方形边长是面积的算术平方根，√81=9。"},
            {"prompt": "下列等式正确的是？", "options": ["√25=±5", "(√5)²=5", "√(a²)=a 对所有实数成立", "√0=-0"], "answer_index": 1, "difficulty": "易错", "explanation": "√25 表示算术平方根，只取 5；(√5)²=5。"},
        ],
        "立方根": [
            {"prompt": "∛(-8) 的值是？", "options": ["-4", "-2", "2", "4"], "answer_index": 1, "difficulty": "基础", "explanation": "(-2)³=-8，所以 ∛(-8)=-2。"},
            {"prompt": "若 ∛(x-1)=3，则 x 的值是？", "options": ["9", "10", "27", "28"], "answer_index": 3, "difficulty": "进阶", "explanation": "x-1=27，所以 x=28。"},
            {"prompt": "下列说法正确的是？", "options": ["负数没有立方根", "一个数有两个立方根", "任何实数都有唯一的立方根", "立方根一定是正数"], "answer_index": 2, "difficulty": "定义", "explanation": "每个实数都有唯一的实数立方根。"},
            {"prompt": "若一个正方体的体积为 125 cm³，则棱长是？", "options": ["4 cm", "5 cm", "25 cm", "125 cm"], "answer_index": 1, "difficulty": "应用", "explanation": "棱长是体积的立方根，∛125=5。"},
            {"prompt": "下列等式正确的是？", "options": ["∛27=±3", "∛(-27)=3", "(∛5)³=5", "∛0=1"], "answer_index": 2, "difficulty": "易错", "explanation": "立方根没有正负两个值，(∛5)³=5。"},
        ],
        "因式分解": [
            {"prompt": "因式分解：3x²-12x。", "options": ["3x(x-4)", "3x(x-12)", "x(3x-12)", "3(x²-4)"], "answer_index": 0, "difficulty": "基础", "explanation": "提取最大公因式 3x，得 3x(x-4)。"},
            {"prompt": "因式分解：x²-9。", "options": ["(x-9)(x+1)", "(x-3)²", "(x-3)(x+3)", "x(x-9)"], "answer_index": 2, "difficulty": "进阶", "explanation": "利用平方差公式 x²-3²=(x-3)(x+3)。"},
            {"prompt": "下列变形属于因式分解的是？", "options": ["x(x+1)=x²+x", "x²+2x+1=(x+1)²", "(x+1)²=x²+2x+1", "x²-1=x²-1"], "answer_index": 1, "difficulty": "定义", "explanation": "因式分解是把多项式化成几个整式乘积的形式。"},
            {"prompt": "因式分解：x²+6x+9。", "options": ["(x+3)²", "(x-3)²", "(x+9)(x+1)", "x(x+6)+9"], "answer_index": 0, "difficulty": "综合", "explanation": "这是完全平方式，x²+6x+9=(x+3)²。"},
            {"prompt": "因式分解时，通常应先考虑？", "options": ["先展开括号", "先提取公因式", "先把所有字母换成数字", "先约去未知数"], "answer_index": 1, "difficulty": "方法", "explanation": "因式分解通常先观察各项是否有公因式。"},
        ],
        "勾股定理": [
            {"prompt": "直角三角形的两条直角边长为 3 和 4，斜边长为？", "options": ["5", "6", "7", "12"], "answer_index": 0, "difficulty": "基础", "explanation": "斜边平方=3²+4²=25，所以斜边为 5。"},
            {"prompt": "直角三角形斜边长为 13，一条直角边长为 5，另一条直角边长为？", "options": ["8", "10", "12", "18"], "answer_index": 2, "difficulty": "进阶", "explanation": "另一条直角边平方=13²-5²=144，所以长度为 12。"},
            {"prompt": "关于勾股定理，下列说法正确的是？", "options": ["只适用于任意三角形", "适用于直角三角形，斜边平方等于两直角边平方和", "三边长度直接相加", "直角边平方等于斜边平方和"], "answer_index": 1, "difficulty": "定义", "explanation": "勾股定理描述的是直角三角形三边之间的平方关系。"},
            {"prompt": "一架梯子长 10 m，底端离墙 6 m，顶端距地面高度为？", "options": ["4 m", "6 m", "8 m", "16 m"], "answer_index": 2, "difficulty": "应用", "explanation": "高度平方=10²-6²=64，所以高度为 8 m。"},
            {"prompt": "若直角三角形两直角边同时扩大为原来的 2 倍，则斜边扩大为原来的？", "options": ["1 倍", "2 倍", "3 倍", "4 倍"], "answer_index": 1, "difficulty": "综合", "explanation": "三边按同一比例扩大，斜边也扩大 2 倍。"},
        ],
        "一次函数": [
            {"prompt": "下列函数中属于一次函数的是？", "options": ["y=2x+3", "y=x²+1", "y=2/x", "y=√x"], "answer_index": 0, "difficulty": "定义", "explanation": "一次函数形式为 y=kx+b，且 k≠0。"},
            {"prompt": "一次函数 y=2x-1 在 x=3 时的函数值是？", "options": ["3", "5", "6", "7"], "answer_index": 1, "difficulty": "基础", "explanation": "代入 x=3，得 y=2×3-1=5。"},
            {"prompt": "直线 y=3x+2 的图象经过哪个点？", "options": ["(0,2)", "(2,0)", "(1,3)", "(-1,1)"], "answer_index": 0, "difficulty": "进阶", "explanation": "令 x=0，得 y=2，因此经过 (0,2)。"},
            {"prompt": "一次函数 y=-2x+5 中，函数值随 x 增大而？", "options": ["增大", "减小", "不变", "先增大后减小"], "answer_index": 1, "difficulty": "性质", "explanation": "一次函数 y=kx+b 中，k<0 时 y 随 x 增大而减小。"},
            {"prompt": "直线 y=2x+b 经过点 (1,5)，则 b=？", "options": ["1", "2", "3", "5"], "answer_index": 2, "difficulty": "综合", "explanation": "代入点坐标得 5=2+b，所以 b=3。"},
        ],
        "一元二次方程": [
            {"prompt": "方程 x²-5x+6=0 的根是？", "options": ["1 和 6", "2 和 3", "-2 和 -3", "5 和 6"], "answer_index": 1, "difficulty": "基础", "explanation": "x²-5x+6=(x-2)(x-3)，所以根为 2 和 3。"},
            {"prompt": "方程 2x²-8=0 的根是？", "options": ["x=2", "x=-2", "x=±2", "x=±4"], "answer_index": 2, "difficulty": "进阶", "explanation": "2x²=8，x²=4，所以 x=±2。"},
            {"prompt": "下列方程是一元二次方程的是？", "options": ["2x+1=0", "x²-3x+2=0", "1/x=2", "x+y=1"], "answer_index": 1, "difficulty": "定义", "explanation": "一元二次方程只含一个未知数，最高次数为 2。"},
            {"prompt": "若方程 x²-4x+m=0 有一个根为 1，则 m=？", "options": ["1", "2", "3", "4"], "answer_index": 2, "difficulty": "综合", "explanation": "将 x=1 代入得 1-4+m=0，所以 m=3。"},
            {"prompt": "一元二次方程求根时，验根的主要目的是？", "options": ["改变方程次数", "检验所得根是否满足原方程", "使根变成整数", "去掉所有负根"], "answer_index": 1, "difficulty": "方法", "explanation": "验根是将求得的结果代回原方程，检查是否成立。"},
        ],
        "圆周角": [
            {"prompt": "同弧所对的圆心角为 100°，圆周角为？", "options": ["25°", "50°", "100°", "200°"], "answer_index": 1, "difficulty": "基础", "explanation": "同弧所对圆周角等于圆心角的一半。"},
            {"prompt": "同弧所对的两个圆周角之间的关系是？", "options": ["互为余角", "互为补角", "相等", "无法确定"], "answer_index": 2, "difficulty": "定义", "explanation": "同弧所对的圆周角相等。"},
            {"prompt": "直径所对的圆周角是？", "options": ["锐角", "直角", "钝角", "平角"], "answer_index": 1, "difficulty": "性质", "explanation": "直径所对的圆周角是直角。"},
            {"prompt": "若圆周角为 38°，则它所对的圆心角为？", "options": ["19°", "38°", "76°", "152°"], "answer_index": 2, "difficulty": "进阶", "explanation": "圆心角是圆周角的 2 倍，38°×2=76°。"},
            {"prompt": "在同圆中，若两个圆周角所对的弧相等，则这两个圆周角？", "options": ["相等", "互补", "一个是另一个的 2 倍", "无法比较"], "answer_index": 0, "difficulty": "综合", "explanation": "同圆中等弧所对的圆周角相等。"},
        ],
    }
    return specs.get(point)


def build_rows(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (p.grade_id, p.point_name)
               p.grade_id, p.grade_name, p.point_name, p.title, p.chapter_id,
               p.content_id, p.content, p.document_id, p.document_name, p.source_path
        FROM knowledge_point_search p
        ORDER BY p.grade_id, p.point_name, p.content_id
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for grade_id, grade_name, point, title, chapter, content_id, content, document_id, document_name, source_path in rows:
        truth = _clean_sentence(content, point)
        citation = {
            "content_id": content_id,
            "document_id": document_id,
            "document_name": document_name,
            "title": title,
            "chapter": chapter,
            "source_path": source_path,
            "excerpt": truth,
            "grade": grade_name,
        }
        special = _special_questions(point)
        if special:
            for variant, item in enumerate(special, 1):
                result.append({
                    "question_id": _question_id(grade_id, point, variant),
                    "grade_id": grade_id,
                    "knowledge_point": point,
                    "chapter": chapter or title or "",
                    "prompt": item["prompt"],
                    "options": item["options"],
                    "answer_index": item["answer_index"],
                    "explanation": item["explanation"],
                    "citation": citation,
                    "difficulty": item["difficulty"],
                })
            continue
        distractors = [
            f"使用“{point}”时可以忽略题目给出的条件，结论对所有情况都成立。",
            f"只要结果形式相似，就可以直接套用“{point}”，不必检查定义。",
            f"把“{point}”与相反概念混淆后得到的结论，也一定适用于本题。",
        ]
        for variant, prompt_template in enumerate(PROMPTS, 1):
            if variant == 4:
                answer = f"先确认题目满足“{point}”的定义或使用条件，再进行推理或计算。"
                options = [
                    "看到相似符号就直接套用公式，不检查条件。",
                    answer,
                    "只要最后算出一个数字，使用过程是否符合定义都不重要。",
                    "把本知识点的条件全部去掉后，结论仍然自动成立。",
                ]
                answer_index = 1
            elif variant == 5:
                answer = distractors[0]
                options = [truth,
                           f"学习“{point}”时应同时关注定义、条件和结论之间的关系。",
                           answer,
                           f"“{point}”的结论应根据题目给出的具体条件进行判断。"]
                answer_index = 2
            else:
                answer_index = (variant - 1) % 4
                options = distractors[:]
                options.insert(answer_index, truth)
            result.append({
                "question_id": _question_id(grade_id, point, variant),
                "grade_id": grade_id,
                "knowledge_point": point,
                "chapter": chapter or title or "",
                "prompt": prompt_template.format(point=point, title=title or point),
                "options": options,
                "answer_index": answer_index,
                "explanation": (f"该判断忽略了“{point}”的定义或使用条件，因此错误。"
                                 if variant == 5 else f"教材“{title}”中提到：{truth}"),
                "citation": citation,
                "difficulty": ("基础", "基础", "中等", "中等", "提高")[variant - 1],
            })
    return result


def ensure_bank_table(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_question_bank (
            question_id TEXT PRIMARY KEY,
            grade_id TEXT NOT NULL CHECK (grade_id IN ('grade7','grade8','grade9')),
            knowledge_point TEXT NOT NULL,
            chapter TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL,
            options JSONB NOT NULL, answer_index INTEGER NOT NULL CHECK (answer_index >= 0),
            explanation TEXT NOT NULL DEFAULT '', citation JSONB NOT NULL DEFAULT '{}'::jsonb,
            difficulty TEXT NOT NULL DEFAULT '基础', active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS practice_question_bank_grade_point_idx ON practice_question_bank(grade_id, knowledge_point, active)")


def seed_question_bank(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as conn:
        ensure_bank_table(conn)
        rows = build_rows(conn)
        conn.cursor().executemany(
            """
            INSERT INTO practice_question_bank
              (question_id, grade_id, knowledge_point, chapter, prompt, options,
               answer_index, explanation, citation, difficulty, active, updated_at)
            VALUES (%(question_id)s,%(grade_id)s,%(knowledge_point)s,%(chapter)s,%(prompt)s,
                    %(options)s::jsonb,%(answer_index)s,%(explanation)s,%(citation)s::jsonb,
                    %(difficulty)s,TRUE,NOW())
            ON CONFLICT (question_id) DO UPDATE SET
              grade_id=EXCLUDED.grade_id, knowledge_point=EXCLUDED.knowledge_point,
              chapter=EXCLUDED.chapter, prompt=EXCLUDED.prompt, options=EXCLUDED.options,
              answer_index=EXCLUDED.answer_index, explanation=EXCLUDED.explanation,
              citation=EXCLUDED.citation, difficulty=EXCLUDED.difficulty, active=TRUE, updated_at=NOW()
            """,
            [{**row, "options": json.dumps(row["options"], ensure_ascii=False),
              "citation": json.dumps(row["citation"], ensure_ascii=False)} for row in rows],
        )
        ids = [row["question_id"] for row in rows]
        if ids:
            conn.execute("UPDATE practice_question_bank SET active=FALSE, updated_at=NOW() WHERE question_id LIKE 'bank_%%' AND NOT (question_id = ANY(%s))", (ids,))
        counts = conn.execute("SELECT COUNT(*), COUNT(DISTINCT (grade_id, knowledge_point)) FROM practice_question_bank WHERE active").fetchone()
        minimum = conn.execute("SELECT COALESCE(MIN(n),0) FROM (SELECT grade_id, knowledge_point, COUNT(*) n FROM practice_question_bank WHERE active GROUP BY grade_id, knowledge_point) q").fetchone()[0]
    return {"questions": counts[0], "grade_knowledge_points": counts[1], "minimum_per_point": minimum}
