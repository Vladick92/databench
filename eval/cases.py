"""Text-to-SQL eval set for the model benchmark (eval/bench.py).

Scoped to plain codegen quality (schema + question -> SQL), not the full agentic tool-calling
loop - a live check (see bench findings) showed Q2_K models don't reliably emit the structured
<tool_call> format the Qwen chat template expects, which would conflate "can it write SQL" with
"can it follow the tool-call protocol". Both matter, but this measures the first in isolation;
the second is noted qualitatively in the benchmark writeup.

Each case's `gold_sql` is executed against the same DuckDB-registered table as the candidate's
SQL; results are compared as sorted, rounded row tuples so ordering/formatting differences don't
cause false failures.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    source_name: str  # "customers" or "products" - must match data/<name>.{csv,xlsx}
    question: str
    gold_sql: str


CASES: list[EvalCase] = [
    EvalCase("count_all", "customers", "How many customers are there in total?", "SELECT COUNT(*) FROM customers"),
    EvalCase(
        "filter_city",
        "customers",
        "How many customers live in Kyiv?",
        "SELECT COUNT(*) FROM customers WHERE city = 'Kyiv'",
    ),
    EvalCase(
        "avg_filtered",
        "customers",
        "What is the average age of active customers? Round to 1 decimal place.",
        "SELECT ROUND(AVG(age), 1) FROM customers WHERE is_active = true",
    ),
    EvalCase(
        "top_n",
        "products",
        "List the 5 most expensive products, showing product_name and price.",
        "SELECT product_name, price FROM products ORDER BY price DESC LIMIT 5",
    ),
    EvalCase(
        "sum_filtered",
        "products",
        "What is the total units_in_stock for the Electronics category?",
        "SELECT SUM(units_in_stock) FROM products WHERE category = 'Electronics'",
    ),
    EvalCase(
        "count_lt",
        "products",
        "How many products cost less than 50?",
        "SELECT COUNT(*) FROM products WHERE price < 50",
    ),
    EvalCase(
        "group_by_top1",
        "customers",
        "Which city has the most customers? Return the city name and the count.",
        "SELECT city, COUNT(*) AS n FROM customers GROUP BY city ORDER BY n DESC LIMIT 1",
    ),
    EvalCase(
        "count_distinct",
        "products",
        "How many distinct product categories are there?",
        "SELECT COUNT(DISTINCT category) FROM products",
    ),
]
