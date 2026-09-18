from __future__ import annotations

import random
import secrets
from dataclasses import dataclass

from .models import Chunk


# The markdown corpus is a teaching reference, not a question bank.  These
# templates turn the most common sections into actual exercises.  The answer
# is kept as text so options can still be shuffled before the quiz is shown.
QUESTION_TEMPLATES: dict[str, list[dict[str, object]]] = {
    "分数": [{"prompt": "将 18/24 约分为最简分数，结果是？", "options": ["3/4", "2/3", "4/5", "5/6"], "answer": "3/4", "explanation": "18 和 24 的最大公因数是 6，同时除以 6 得 3/4。"}],
    "一元一次方程": [{"prompt": "解方程 3x + 5 = 20，x 的值是？", "options": ["3", "5", "7", "25/3"], "answer": "5", "explanation": "两边同时减 5 得 3x=15，再除以 3 得 x=5。"}],
    "正数和负数": [{"prompt": "计算：-3 + 8 =？", "options": ["-11", "-5", "5", "11"], "answer": "5", "explanation": "异号相加，取绝对值较大的正号，并用 8-3=5。"}],
    "绝对值": [{"prompt": "|-7| 的值是？", "options": ["-7", "0", "7", "14"], "answer": "7", "explanation": "绝对值表示数轴上到原点的距离，因此 |-7|=7。"}],
    "有理数的大小比较": [{"prompt": "下列各数中最大的是？", "options": ["-2", "-1/2", "0", "-3"], "answer": "0", "explanation": "0 大于所有负数。"}],
    "有理数的加法": [{"prompt": "计算：(-3) + (-5) =？", "options": ["-8", "-2", "2", "8"], "answer": "-8", "explanation": "同号相加取相同符号，绝对值相加，结果为 -8。"}],
    "有理数的减法": [{"prompt": "计算：7 - (-3) =？", "options": ["4", "-4", "10", "-10"], "answer": "10", "explanation": "减去一个负数等于加上它的相反数：7-(-3)=7+3=10。"}],
    "有理数的乘法": [{"prompt": "计算：(-2)×(-5) =？", "options": ["-10", "-7", "7", "10"], "answer": "10", "explanation": "同号相乘得正，2×5=10。"}],
    "有理数的除法": [{"prompt": "计算：(-12)÷3 =？", "options": ["-4", "-3", "4", "36"], "answer": "-4", "explanation": "异号相除得负，12÷3=4。"}],
    "有理数的乘方": [{"prompt": "计算：(-2)^3 =？", "options": ["-8", "-6", "6", "8"], "answer": "-8", "explanation": "负数的奇次幂为负，(-2)^3=-8。"}],
    "科学记数法与近似数": [{"prompt": "将 0.00045 用科学记数法表示，结果是？", "options": ["4.5×10^-4", "45×10^-5", "4.5×10^4", "0.45×10^-3"], "answer": "4.5×10^-4", "explanation": "小数点向右移动 4 位得到 4.5，因此指数为 -4。"}],
    "有理数的混合运算": [{"prompt": "计算：3 + 2×4 =？", "options": ["20", "14", "11", "24"], "answer": "11", "explanation": "先乘除后加减：3+8=11。"}],
    "列代数式与代数式的值": [{"prompt": "当 a=4 时，代数式 2a+3 的值是？", "options": ["8", "10", "11", "14"], "answer": "11", "explanation": "把 a=4 代入：2×4+3=11。"}],
    "同类项": [{"prompt": "合并同类项：3x+2x =？", "options": ["5x", "6x", "5x^2", "x"], "answer": "5x", "explanation": "同类项只合并系数，3+2=5，字母和指数不变。"}],
    "整式的加减": [{"prompt": "化简：3x - (2x - 1) =？", "options": ["x-1", "x+1", "5x-1", "5x+1"], "answer": "x+1", "explanation": "去括号得 3x-2x+1，再合并同类项得 x+1。"}],
    "解一元一次方程": [{"prompt": "解方程 2(x-3)=10，x 的值是？", "options": ["2", "5", "8", "13"], "answer": "8", "explanation": "两边除以 2 得 x-3=5，所以 x=8。"}],
    "实际问题与一元一次方程": [{"prompt": "某商品打八折后售价为 64 元，原价是多少元？", "options": ["76.8", "80", "84", "96"], "answer": "80", "explanation": "设原价为 x，则 0.8x=64，解得 x=80。"}],
    "角": [{"prompt": "一个角的补角是 125°，这个角的度数是？", "options": ["35°", "55°", "65°", "125°"], "answer": "55°", "explanation": "补角和为 180°，所以角度为 180°-125°=55°。"}],
    "平移": [{"prompt": "点 A(2,-1) 向左平移 3 个单位后坐标是？", "options": ["(5,-1)", "(-1,-1)", "(2,2)", "(-1,2)"], "answer": "(-1,-1)", "explanation": "向左平移只改变横坐标：2-3=-1。"}],
    "平方根": [{"prompt": "√49 的值是？", "options": ["-7", "-1", "7", "49"], "answer": "7", "explanation": "算术平方根规定为非负数，√49=7。"}],
    "立方根": [{"prompt": "∛(-8) 的值是？", "options": ["-4", "-2", "2", "4"], "answer": "-2", "explanation": "(-2)^3=-8，所以 ∛(-8)=-2。"}],
    "平面直角坐标系": [{"prompt": "点 P(-2,3) 的横坐标和纵坐标分别是？", "options": ["-2，3", "2，3", "-2，-3", "3，-2"], "answer": "-2，3", "explanation": "有序数对 (x,y) 中第一项是横坐标，第二项是纵坐标。"}],
    "二元一次方程（组）": [{"prompt": "方程组 x+y=7，x-y=1 的解是？", "options": ["x=3,y=4", "x=4,y=3", "x=5,y=2", "x=6,y=1"], "answer": "x=4,y=3", "explanation": "两式相加得 2x=8，所以 x=4，再得 y=3。"}],
    "一元一次不等式": [{"prompt": "不等式 2x-3>5 的解集是？", "options": ["x>1", "x>4", "x<4", "x<1"], "answer": "x>4", "explanation": "移项得 2x>8，两边除以正数 2，得 x>4。"}],
    "统计图表": [{"prompt": "一组数据 2，3，3，5，7 的众数是？", "options": ["2", "3", "4", "5"], "answer": "3", "explanation": "众数是出现次数最多的数，3 出现了两次。"}],
    "三角形的三边关系": [{"prompt": "下列三条线段能组成三角形的是？", "options": ["2，3，6", "3，4，7", "4，5，8", "1，2，4"], "answer": "4，5，8", "explanation": "较短两边之和必须大于最长边，4+5>8。"}],
    "三角形的内角和": [{"prompt": "一个三角形的两个内角分别为 48° 和 67°，第三个内角是？", "options": ["55°", "65°", "75°", "85°"], "answer": "65°", "explanation": "三角形内角和为 180°，第三角=180°-48°-67°=65°。"}],
    "三角形的外角": [{"prompt": "三角形的两个不相邻内角为 45° 和 70°，对应外角为？", "options": ["25°", "95°", "115°", "160°"], "answer": "115°", "explanation": "三角形外角等于不相邻两内角之和：45°+70°=115°。"}],
    "等腰三角形": [{"prompt": "等腰三角形的顶角为 40°，则每个底角为？", "options": ["40°", "50°", "70°", "80°"], "answer": "70°", "explanation": "两底角相等，且底角和为 180°-40°=140°，每个为 70°。"}],
    "等边三角形": [{"prompt": "等边三角形的每个内角是多少度？", "options": ["30°", "45°", "60°", "90°"], "answer": "60°", "explanation": "三个内角相等且和为 180°，每个内角为 60°。"}],
    "幂的运算": [{"prompt": "化简：a^3·a^2 =？", "options": ["a^5", "a^6", "2a^5", "a"], "answer": "a^5", "explanation": "同底数幂相乘，底数不变，指数相加。"}],
    "乘法公式": [{"prompt": "计算：(x+3)(x-3) =？", "options": ["x^2-9", "x^2+9", "x^2-6x+9", "x^2+6x+9"], "answer": "x^2-9", "explanation": "使用平方差公式 (a+b)(a-b)=a^2-b^2。"}],
    "因式分解": [{"prompt": "因式分解：x^2-9 =？", "options": ["(x-9)(x+1)", "(x-3)^2", "(x-3)(x+3)", "x(x-9)"], "answer": "(x-3)(x+3)", "explanation": "x^2-9 是平方差，分解为 (x-3)(x+3)。"}],
    "分式的运算": [{"prompt": "计算：1/3 + 1/6 =？", "options": ["1/9", "1/2", "2/3", "1"], "answer": "1/2", "explanation": "通分为 2/6+1/6=3/6=1/2。"}],
    "勾股定理": [{"prompt": "直角三角形两直角边长为 3 和 4，斜边长为？", "options": ["5", "6", "7", "12"], "answer": "5", "explanation": "由勾股定理 c^2=3^2+4^2=25，所以 c=5。"}],
    "平行四边形的性质": [{"prompt": "平行四边形的一条边长为 6 cm，则与它相对的边长为？", "options": ["3 cm", "6 cm", "12 cm", "无法确定"], "answer": "6 cm", "explanation": "平行四边形的对边相等。"}],
    "矩形": [{"prompt": "矩形的对角线长相等。一条对角线为 10 cm，另一条为？", "options": ["5 cm", "10 cm", "20 cm", "无法确定"], "answer": "10 cm", "explanation": "矩形的两条对角线相等。"}],
    "一次函数": [{"prompt": "一次函数 y=2x-1 在 x=3 时的函数值是？", "options": ["5", "6", "7", "-5"], "answer": "5", "explanation": "代入 x=3：y=2×3-1=5。"}],
    "平均数": [{"prompt": "数据 4，6，8，10 的平均数是？", "options": ["6", "7", "8", "9"], "answer": "7", "explanation": "平均数=(4+6+8+10)÷4=7。"}],
    "一元二次方程": [{"prompt": "方程 x^2-5x+6=0 的根是？", "options": ["x=1 或 6", "x=2 或 3", "x=-2 或 -3", "x=5 或 6"], "answer": "x=2 或 3", "explanation": "因式分解为 (x-2)(x-3)=0，所以 x=2 或 3。"}],
    "根的判别式": [{"prompt": "一元二次方程 x^2+2x+3=0 的实根个数是？", "options": ["0 个", "1 个", "2 个", "无法判断"], "answer": "0 个", "explanation": "判别式 Δ=2^2-4×1×3=-8<0，所以没有实根。"}],
    "二次函数的顶点式": [{"prompt": "抛物线 y=(x-2)^2+3 的顶点坐标是？", "options": ["(-2,3)", "(2,-3)", "(2,3)", "(3,2)"], "answer": "(2,3)", "explanation": "顶点式 y=(x-h)^2+k 的顶点为 (h,k)。"}],
    "圆周角": [{"prompt": "同弧所对的圆心角为 100°，圆周角为？", "options": ["25°", "50°", "100°", "200°"], "answer": "50°", "explanation": "同弧所对圆周角等于圆心角的一半。"}],
    "随机事件与概率": [{"prompt": "掷一枚公平骰子，掷出偶数的概率是？", "options": ["1/6", "1/3", "1/2", "2/3"], "answer": "1/2", "explanation": "偶数有 2、4、6 共 3 种，概率为 3/6=1/2。"}],
    "反比例函数": [{"prompt": "若 y=6/x，则 x=2 时 y 的值是？", "options": ["2", "3", "4", "12"], "answer": "3", "explanation": "代入 y=6÷2=3。"}],
    "相似三角形的性质": [{"prompt": "两个相似三角形的相似比为 2:3，则面积比为？", "options": ["2:3", "4:9", "6:9", "8:27"], "answer": "4:9", "explanation": "相似三角形面积比等于相似比的平方。"}],
    "锐角三角函数": [{"prompt": "在直角三角形中，sin A 等于？", "options": ["邻边/斜边", "对边/斜边", "对边/邻边", "斜边/对边"], "answer": "对边/斜边", "explanation": "正弦定义为锐角的对边与斜边之比。"}],
}


@dataclass(slots=True)
class PracticeQuestion:
    question_id: str
    prompt: str
    options: list[str]
    answer: int
    explanation: str
    chunk: Chunk

    def to_public_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "prompt": self.prompt,
            "options": self.options,
            "knowledge_point": self.chunk.knowledge_point_ids[0] if self.chunk.knowledge_point_ids else self.chunk.title,
            "chapter": self.chunk.chapter_id,
            "difficulty": self.chunk.metadata.get("difficulty", ""),
        }


@dataclass(slots=True)
class PracticeQuiz:
    quiz_id: str
    student_id: str
    grade_id: str
    questions: list[PracticeQuestion]
    submitted: bool = False


class PracticeService:
    """Traceable multiple-choice practice generated from indexed教材 chunks."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.quizzes: dict[str, PracticeQuiz] = {}

    @staticmethod
    def _fallback_question(chunk: Chunk) -> dict[str, object]:
        """Make a comprehension item from a section that has no hand-written template."""
        correct = " ".join(
            line.strip() for line in chunk.text.splitlines()
            if line.strip() and not line.strip().startswith(("知识点：", "知识点:"))
        )
        correct = correct[:150].rstrip("。；; ") + "。"
        return {
            "prompt": f"根据教材内容，关于“{chunk.title}”的说法，正确的是？",
            "options": [
                correct,
                "该结论与教材内容相反。",
                "该结论只在题目给出其他条件时才成立。",
                "教材没有给出这一知识点的相关结论。",
            ],
            "answer": correct,
            "explanation": f"教材中“{chunk.title}”一节指出：{correct}",
        }

    @classmethod
    def _question_for_chunk(cls, chunk: Chunk) -> dict[str, object]:
        configured_options = list(chunk.metadata.get("options") or [])
        configured_answer = chunk.metadata.get("answer_index")
        if configured_options and configured_answer is not None:
            answer_text = (configured_options[configured_answer]
                           if isinstance(configured_answer, int) and configured_answer < len(configured_options)
                           else configured_options[0])
            return {
                "prompt": chunk.metadata.get("question_stem") or "请完成这道题：",
                "options": configured_options[:4],
                "answer": answer_text,
                "explanation": chunk.metadata.get("explanation") or chunk.text[:280],
            }
        template = QUESTION_TEMPLATES.get(chunk.title)
        return dict(template[0]) if template else cls._fallback_question(chunk)

    def create_quiz(self, student_id: str, grade_id: str = "grade7", count: int = 5,
                    difficulty: str | None = None, knowledge_point: str | None = None) -> PracticeQuiz:
        if grade_id not in {"grade7", "grade8", "grade9"}:
            raise ValueError("grade_id 必须是 grade7、grade8 或 grade9")
        count = max(1, min(int(count), 10))
        pool = [chunk for chunk in self.chunks if chunk.metadata.get("grade_id") == grade_id]
        if knowledge_point:
            pool = [chunk for chunk in pool if knowledge_point in chunk.knowledge_point_ids or chunk.title == knowledge_point]
        if difficulty:
            filtered = [chunk for chunk in pool if str(chunk.metadata.get("difficulty", "")) == difficulty]
            if filtered:
                pool = filtered
        if not pool:
            raise ValueError("该年级暂无可用练习内容")
        # Every教材 section can produce an item. Prefer configured question-bank
        # rows and curated exercises so a normal five-question set does not
        # fall back to comprehension-only items unless the pool is tiny.
        preferred = [chunk for chunk in pool if chunk.metadata.get("options") and
                     chunk.metadata.get("answer_index") is not None or chunk.title in QUESTION_TEMPLATES]
        selection_pool = preferred if len(preferred) >= count else pool
        selected = random.Random(f"{student_id}:{grade_id}:{len(self.quizzes)}").sample(
            selection_pool, min(count, len(selection_pool)))
        questions: list[PracticeQuestion] = []
        for number, chunk in enumerate(selected, 1):
            item = self._question_for_chunk(chunk)
            options = [str(option) for option in item["options"]]
            random.Random(f"{student_id}:{grade_id}:{number}:{chunk.chunk_id}").shuffle(options)
            answer_value = str(item["answer"])
            questions.append(PracticeQuestion(
                question_id=f"q{number}",
                prompt=str(item["prompt"]),
                options=options,
                answer=options.index(answer_value),
                explanation=str(item["explanation"]),
                chunk=chunk,
            ))
        quiz = PracticeQuiz(secrets.token_urlsafe(12), student_id, grade_id, questions)
        self.quizzes[quiz.quiz_id] = quiz
        return quiz

    def submit(self, quiz_id: str, student_id: str, answers: dict[str, int]) -> dict:
        quiz = self.quizzes.get(quiz_id)
        if not quiz or quiz.student_id != student_id or quiz.submitted:
            raise KeyError(quiz_id)
        results = []
        correct_count = 0
        for question in quiz.questions:
            selected = answers.get(question.question_id)
            is_correct = selected == question.answer
            correct_count += int(is_correct)
            results.append({
                "question_id": question.question_id,
                "selected": selected,
                "correct": question.answer,
                "is_correct": is_correct,
                "explanation": question.explanation,
                "citation": {"chunk_id": question.chunk.chunk_id, "document_id": question.chunk.document_id,
                              "title": question.chunk.title, "source_path": question.chunk.source_path,
                              "excerpt": question.chunk.text[:240]},
                "knowledge_point": question.chunk.knowledge_point_ids[0] if question.chunk.knowledge_point_ids else question.chunk.title,
            })
        quiz.submitted = True
        return {"quiz_id": quiz_id, "score": correct_count, "total": len(quiz.questions), "results": results}
