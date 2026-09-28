from .execute_sql_query import execute_sql_query
from .execute_table_code import execute_table_code
from .list_data_sources import list_data_sources

TOOLS = [list_data_sources, execute_sql_query, execute_table_code]
