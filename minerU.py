import requests

token = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiI3MzAwMDEzMCIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3ODY1OTE5NywiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiIiwib3BlbklkIjpudWxsLCJ1dWlkIjoiYmIxMWNlYzktNTJkOC00OGJhLWEyODMtM2UxZDYwMDgwN2ExIiwiZW1haWwiOiIiLCJleHAiOjE3ODY0MzUxOTd9.2fwiDtQMPqr3Mx4TgojTjchdHTmA6k3r5EGD49QZXUbqRfytN6Oxv0Bj0nqmdnnyNhjBBs0G4RVvfmRA1gWE1Q"
url = "https://mineru.net/api/v4/extract/task"
header = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}
data = {
    "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
    "model_version": "vlm"
}

res = requests.post(url,headers=header,json=data)
print(res.status_code)
print(res.json())
print(res.json()["data"])