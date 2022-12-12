import random
from datetime import datetime, timedelta

from airflow import DAG
from airflow.hooks.base import BaseHook
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.models import Variable, Connection
from functools import reduce
from pathlib import Path
from airflow.sensors.filesystem import FileSensor
from airflow.sensors.python import PythonSensor
from airflow.providers.postgres.operators.postgres import PostgresOperator
import psycopg2


def _get_conn_db() -> Connection:
    return BaseHook.get_connection("db")


def insert_data_to_db():
    global cursor, connection
    try:
        cred = _get_conn_db()
        connection = psycopg2.connect(user=cred.login,
                                      password=cred.password,
                                      host=cred.host,
                                      port=cred.port,
                                      database=cred.schema)
        cursor = connection.cursor()
        path_to_file = Variable.get("file_path")
        with open(path_to_file, "r") as file:
            lines = file.readlines()[:-1]
            sql = ""
            for line in lines:
                spl_line = line.split(" ")
                if len(spl_line) > 1:
                    s = f"insert into task(value_1, value_2) VALUES ({spl_line[0]},{spl_line[1]});"
                    sql += s
            print(sql)
            cursor.execute(sql)
        connection.commit()
    finally:
        if connection:
            cursor.close()
            connection.close()
            print("PostgreSQL connection is closed")


def hello():
    print("Airflow")


def generate_two_number_and_add_to_file():
    generated_numbers = random.randint(1, 10), random.randint(1, 10)
    path_to_file = Variable.get("file_path")
    with open(path_to_file, "a") as file:
        file.write(f'{generated_numbers[0]} {generated_numbers[1]}\n')


def sum_column_and_diff() -> int:
    path_to_file = Variable.get("file_path")
    result: int = 0
    with open(path_to_file, "r") as file:
        lines = file.readlines()
        if len(lines) > 1:
            splinted_row = map(lambda x: x.split(" "), lines)
            prepare_lines = map(lambda x:
                                (int(x[0]), int(x[1].replace("\n", ""))),
                                filter(lambda x: len(x) > 1, splinted_row))
            sum_columns = reduce(lambda a, b: (a[0] + b[0], a[1] + b[1]), prepare_lines)
            result = sum_columns[0] - sum_columns[1]
    print(f"Different sum column is {result}")
    return result


def remove_last_sum(path):
    with open(path, "r") as f:
        lines: [str] = filter(lambda line: len(line.split(" ")) > 1, f.readlines())
        with open(path, "w") as fw:
            fw.writelines(lines)


def write_diff(**kwargs):
    ti = kwargs['ti']
    res = ti.xcom_pull(task_ids='sum_column_task')
    path_to_file = Variable.get("file_path")
    remove_last_sum(path_to_file)

    with open(path_to_file, "a") as f:
        f.write(f"{res}\n")


def _get_len_file(path) -> int:
    with open(path, "r") as f:
        lines = f.readlines()
        return len(lines) - 1


def _res_is_right(path) -> bool:
    res = False
    with open(path, "r") as f:
        res = sum_column_and_diff()
        last_row = f.readlines()[-1]
        res = int(res) == len(last_row)
    return res


def _wait_for_file() -> bool:
    path_to_file: Path = Path(Variable.get("file_path"))
    print(f"_wait_for_file {path_to_file.exists()}")
    return path_to_file.exists() and _get_len_file(path_to_file) == 5
    #  and _res_is_right(path_to_file)


def check_file_exiat():
    path_to_file: Path = Path(Variable.get("file_path"))

    if path_to_file.exists():
        return "create_task_table"
    else:
        return "bash_error"


with DAG(dag_id="first_dag",
         description="A simple tutorial DAG",
         schedule_interval='* * * * *',

         start_date=datetime.now(),
         end_date=datetime.now() + timedelta(minutes=5),
         catchup=False,

         tags=["first"]) as dag:
    bash_task = BashOperator(task_id="hello_task", bash_command="echo hello")
    bash_error = BashOperator(task_id="bash_error", bash_command="file not exist")
    python_task = PythonOperator(task_id="world", python_callable=hello)
    generate_number = PythonOperator(task_id="generate_two_int_task",
                                     python_callable=generate_two_number_and_add_to_file)
    different_sum_task = PythonOperator(task_id="sum_column_task", python_callable=sum_column_and_diff)
    write_diff_operator = PythonOperator(task_id="write_diff_task", python_callable=write_diff)

    wait_for_file_task = PythonSensor(
        task_id="wait_for_file",
        python_callable=_wait_for_file,
        poke_interval=20,
        retries=2
    )
    branch_operator = BranchPythonOperator(
        task_id='branch_operator',
        python_callable=check_file_exiat
    )

    create_task_table = PostgresOperator(
        task_id="create_task_table",
        postgres_conn_id="db",
        sql="""
                create table if not exists task (
                id SERIAL,
	            value_1 INT,
	            value_2 INT
            );
              """,
    )
    alter_task_table = PostgresOperator(
        task_id="alter_task_table",
        postgres_conn_id="db",
        sql="""
              alter table task add column if not exists coef int;
                  """,
    )
    update_task_table = PostgresOperator(
        task_id="update_task_table",
        postgres_conn_id="db",
        sql="""
               UPDATE task
                SET coef = t.remainder from (
                select  id, value_1,value_2,
                sum(value_1) over (order by value_1 rows between unbounded preceding and current row)-
                sum(value_2) over (order by value_2 rows between unbounded preceding and current row) as remainder
                from task) as t where task.id=t.id;
                  """,
    )

    insert_data_to_db_task = PythonOperator(task_id="insert_data", python_callable=insert_data_to_db)

    bash_task >> python_task >> generate_number >> different_sum_task \
    >> write_diff_operator >> wait_for_file_task >> branch_operator >>\
    [create_task_table >> insert_data_to_db_task >> alter_task_table>>update_task_table, bash_error]
