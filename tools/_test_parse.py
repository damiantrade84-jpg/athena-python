import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_utils import parse_json_object

test1 = '{"grade":"A","edgeProbability":0.85}'
print("Test1:", parse_json_object(test1))

test2 = ""
print("Test2 empty:", parse_json_object(test2))
