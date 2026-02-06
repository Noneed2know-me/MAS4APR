"""
generate jeorn project on a joern server
"""
import os

from cpgqls_client import CPGQLSClient

server_endpoint = "127.0.0.1:8081"   # server url
client = CPGQLSClient(server_endpoint)

def get_repos(root_dir, proj_list, id_list):
    repos_dir = root_dir + 'defects4j/'
    for i in range(len(proj_list)):
        project = proj_list[i]
        for j in id_list[i]:
            unique_id = project + '-' + str(j)
            try:
                print("in processing: " + project + '_' + str(j))
                id = './' + unique_id + '_buggy'
                name = unique_id + '_buggy'
                query = f'importCode(inputPath="{id}", projectName="{name}")'
                print(query)
                result = client.execute(query)
                print(result)
            except (RuntimeError, TypeError, NameError, FileNotFoundError) as e:
                print(e)


def get_repos_by_id(proj_id):
    try:
        name = proj_id.split('-')[0]
        id = proj_id.split('-')[1]
        proj_id_buggy = name + "-" + id + '_buggy'
        proj_path = '/tmp/' + proj_id_buggy + '/'
        print("in processing: " + proj_id)
        query = f'importCode(inputPath="{proj_path}", projectName="{proj_id_buggy}")'
        print(query)
        result = client.execute(query)
        print(result)
    except (RuntimeError, TypeError, NameError, FileNotFoundError) as e:
        print(e)



root_dir = '/tmp/'
#d4j_dir = '/defects4j_v2.0/'

list = ['Chart-1', 'Chart-10', 'Chart-11', 'Chart-12', 'Chart-13', 'Chart-17', 'Chart-20', 'Chart-23', 'Chart-24', 'Chart-26', 'Chart-3', 'Chart-4', 'Chart-5', 'Chart-6', 'Chart-7', 'Chart-8', 'Chart-9', 'Closure-1', 'Closure-10', 'Closure-101', 'Closure-102', 'Closure-104', 'Closure-105', 'Closure-107', 'Closure-109']
#list = ['Closure-11', 'Closure-111', 'Closure-112', 'Closure-113', 'Closure-114', 'Closure-115', 'Closure-116', 'Closure-117', 'Closure-118', 'Closure-119', 'Closure-12', 'Closure-120', 'Closure-121', 'Closure-122', 'Closure-123', 'Closure-124', 'Closure-125', 'Closure-126', 'Closure-127', 'Closure-128', 'Closure-129', 'Closure-13', 'Closure-130', 'Closure-131', 'Closure-132', 'Closure-133', 'Closure-14', 'Closure-15', 'Closure-17', 'Closure-18', 'Closure-19', 'Closure-2', 'Closure-20', 'Closure-21', 'Closure-22', 'Closure-23', 'Closure-24', 'Closure-25', 'Closure-28', 'Closure-29', 'Closure-31', 'Closure-32', 'Closure-33', 'Closure-35', 'Closure-36', 'Closure-38', 'Closure-39', 'Closure-40', 'Closure-42', 'Closure-44', 'Closure-48', 'Closure-5', 'Closure-50', 'Closure-51', 'Closure-52', 'Closure-53', 'Closure-55', 'Closure-56', 'Closure-57', 'Closure-58', 'Closure-59', 'Closure-61', 'Closure-62', 'Closure-65', 'Closure-66', 'Closure-67', 'Closure-69', 'Closure-7', 'Closure-70', 'Closure-71', 'Closure-73', 'Closure-77', 'Closure-78', 'Closure-8', 'Closure-81', 'Closure-82', 'Closure-83', 'Closure-86', 'Closure-87', 'Closure-88', 'Closure-91', 'Closure-92', 'Closure-94', 'Closure-95', 'Closure-96', 'Closure-97', 'Closure-99', 'Lang-1', 'Lang-10', 'Lang-11', 'Lang-12', 'Lang-14', 'Lang-16', 'Lang-17', 'Lang-18', 'Lang-19', 'Lang-21', 'Lang-22', 'Lang-24', 'Lang-26', 'Lang-27', 'Lang-28', 'Lang-29', 'Lang-3', 'Lang-31', 'Lang-33', 'Lang-37', 'Lang-38', 'Lang-39', 'Lang-4', 'Lang-40', 'Lang-42', 'Lang-43', 'Lang-44', 'Lang-45', 'Lang-48', 'Lang-49', 'Lang-5', 'Lang-51', 'Lang-52', 'Lang-53', 'Lang-54', 'Lang-55', 'Lang-57', 'Lang-58', 'Lang-59', 'Lang-6', 'Lang-61', 'Lang-65', 'Lang-9', 'Math-10', 'Math-101', 'Math-102', 'Math-103', 'Math-104', 'Math-105', 'Math-106', 'Math-11', 'Math-13', 'Math-15', 'Math-16', 'Math-17', 'Math-19', 'Math-2', 'Math-20', 'Math-21', 'Math-23', 'Math-24', 'Math-25', 'Math-26', 'Math-27', 'Math-28', 'Math-3', 'Math-30', 'Math-31', 'Math-32', 'Math-33', 'Math-34', 'Math-38', 'Math-39', 'Math-40', 'Math-41', 'Math-42', 'Math-43', 'Math-44', 'Math-45', 'Math-48', 'Math-5', 'Math-50', 'Math-51', 'Math-52', 'Math-53', 'Math-55', 'Math-56', 'Math-57', 'Math-58', 'Math-59', 'Math-60', 'Math-61', 'Math-63', 'Math-64', 'Math-69', 'Math-7', 'Math-70', 'Math-72', 'Math-73', 'Math-74', 'Math-75', 'Math-78', 'Math-79', 'Math-8', 'Math-80', 'Math-82', 'Math-84', 'Math-85', 'Math-86', 'Math-87', 'Math-88', 'Math-89', 'Math-9', 'Math-90', 'Math-91', 'Math-94', 'Math-95', 'Math-96', 'Math-97', 'Mockito-1', 'Mockito-12', 'Mockito-13', 'Mockito-15', 'Mockito-18', 'Mockito-2', 'Mockito-20', 'Mockito-22', 'Mockito-24', 'Mockito-26', 'Mockito-27', 'Mockito-28', 'Mockito-29', 'Mockito-3', 'Mockito-31', 'Mockito-32', 'Mockito-33', 'Mockito-34', 'Mockito-36', 'Mockito-37', 'Mockito-38', 'Mockito-5', 'Mockito-7', 'Mockito-8', 'Mockito-9', 'Time-10', 'Time-14', 'Time-15', 'Time-16', 'Time-17', 'Time-18', 'Time-19', 'Time-20', 'Time-22', 'Time-23', 'Time-24', 'Time-25', 'Time-27', 'Time-4', 'Time-5', 'Time-7', 'Time-8', 'Time-9']

for proj in list:
    get_repos_by_id(proj)