import sys
sys.path.append("../../..")
import threading

import base64
import json
import urllib
import urllib2
import datetime
import pymongo
from pymongo import MongoClient
from GithubUser.DMLib.DMDatabase import DMDatabase
from GithubUser.DMLib.DMSharedUsers import DMSharedUsers
from GithubUser.DMLib.DMTask import DMTask

def date_string_to_int(date_string):
    num = int(date_string[0:4])*10000+int(date_string[5:7])*100+int(date_string[8:10])
    return num


class GithubRepo:
    def __init__(self, task):
        self.task = task
        self.db = DMDatabase().getDB()

    def append_repos(self, gh_user_id, page):
        url = "https://api.github.com/users/"+gh_user_id+"/repos";
        return DMSharedUsers().readURL(url, {"page": page})

    def upload_user_repos(self, user_login, user_id, user_count):
        need_update = 1
        old_res = self.db["user_repos"].find_one({"login": user_login})
        if old_res:
            if old_res.has_key("count"):
                old_res_len = old_res["count"]
                if (old_res_len > 0) and (user_count <= old_res_len):
                    print "saved"
                    return 0
            else:
                old_res_len = len(old_res["repos"])
                if (old_res_len > 0) and (user_count <= old_res_len):
                    print "saved, but need to update - add count prop"
                    val = {"$set": {"count": old_res_len}}
                    self.db["user_repos"].update({"login":user_login}, val)
                    return 0
        else:
            need_update = 0

        new_res = self.user_repos_list(user_login, user_count)
        if (new_res["error"] == 1):
#TODO we should save this error in order to do it again!
            return 1

        new_res_count = len(new_res["val"])

        if need_update == 0:
            val = {"login": user_login, 
                   "id":    user_id,
                   "repos": new_res["val"],
                   "count": new_res_count,
                   "update_date": datetime.datetime.utcnow()
                  }
            self.db["user_repos"].insert(val)
            print "insert " + user_login + "new count " + str(new_res_count) + " whole " + str(user_count)
        else:
            val = {"$set": {"update_date": datetime.datetime.utcnow(),
                            "count": new_res_count,
                            "repos": new_res["val"]}}
            self.db["user_repos"].update({"login":user_login}, val)
            print "update " + user_login + "new count " + str(new_res_count) + " whole " + str(user_count)
        return 0

    def user_repos_list(self, user_login, count):
        res = []
        if count <= 0:
            return {"error": 1}
# 30 is github system defined
        page_size = 30
        pages = count/30 + 1
        i = 1
# FIXME: if the page was too big, we cannot save it since the limitation, so we can take it as special case 
# most time, organizations will have huge repo!
        if pages > 60:
            pages = 60
        while i <= pages:
            ret_val = self.append_repos(user_login, i)
            if ret_val["error"] == 1:
                if i > 2:
#   "message": "In order to keep the API fast for everyone, pagination is limited for this resource. Check the rel=last link relation in the Link response header to see how far back you can traverse.",
#  "documentation_url": "https://developer.github.com/v3/#pagination"
                    return {"error": 0, "val": res}
                else:
                    return {"error": 1}
# improve a little!
            if len(ret_val["val"]) > 0:
                res += ret_val["val"]
                if len(ret_val["val"]) < 30:
                    break
            else:
                break
            i += 1

        return {"error": 0, "val": res}

    def validateTask(self):
        info = self.task.getInfo()
        if info["start"] > info["end"]:
            print "Error in the task"
            return 0
        return 1

    def runTask(self):
        info = self.task.getInfo()
        if info["action_type"] == "loop":
            self.runLoopTask()
        elif info["action_type"] == "single":
            self.runSingleTask()

    def runSingleTask(self):
        if self.task.updateStatus("running") != 0:
            return

        info = self.task.getInfo()
        login = info["start"]

        item = self.db["user"].find_one({"login": login})

        r_count = item["public_repos"]
        ret = self.upload_user_repos(item["login"], item["id"], r_count)
        if ret == 1:
#TODO make a better error message
            self.task.error({"login": item["login"], "message": "error in upload_user_repo"})
        else:
            self.task.update({"status": "finish", "percent": 1.0, "update_date": datetime.datetime.utcnow()})
        print "Task finish, exiting the thread"

    def runLoopTask(self):
        if self.validateTask() == 0:
            return
        if self.task.updateStatus("running") != 0:
            return

        info = self.task.getInfo()
        start_id = info["start"]
        end_id = info["end"]
        if info.has_key("current"):
            start_id = info["current"]
            print "Find unfinished task, continue to work at " + str(start_id)

        if end_id <= start_id:
# This should be checked in DMTask
            print "Error in the task"
            return

        query = {"$and": [{"id": {"$gte": start_id, "$lt": end_id}}, {"public_repos": {"$gt": 0}}]}

        res = self.db["user"].find(query).sort("id", pymongo.ASCENDING)
# When the upload takes too long, the cursor will miss
#    cursor.addOption(Bytes.QUERYOPTION_NOTIMEOUT)
# CursorNotFound: cursor id '116709267398' not valid at server
# I should save the id list first
        res_list = []
        for item in res:
            res_list.append({"login": item["login"], "id": item["id"], "public_repos": item["public_repos"]})
        res_len = len(res_list)
        i = 0
        percent_gap = res_len/100

        for item in res_list:
            i += 1
            r_count = item["public_repos"]
            ret = self.upload_user_repos(item["login"], item["id"], r_count)
            if ret == 1:
#TODO make a better error message
                self.task.error({"login": item["login"], "message": "error in upload_user_repo"})
                continue

            if percent_gap == 0:
                percent = 1.0 * i / res_len
                self.task.update({"current": item["id"], "percent": percent, "update_date": datetime.datetime.utcnow()})
#save every 100 calculate 
            elif i%percent_gap == 0:
                percent = 1.0 * i / res_len
                self.task.update({"current": item["id"], "percent": percent, "update_date": datetime.datetime.utcnow()})

        self.task.update({"status": "finish", "current": end_id, "percent": 1.0, "update_date": datetime.datetime.utcnow()})
        print "Task finish, exiting the thread"

# very important, the entry function
def init_repo_task():
# TODO: 1000 is system defined, maybe add to DMTask? or config file?
    gap = 1000
    start = 0
# end id is now set to 10300000
    end = 10300
    start = 10300
    end = 11030

    db = DMDatabase().getDB()
    for i in range (start, end):
        task = DMTask()
        val = {"name": "get_repos", "action_type": "loop", "start": i * gap, "end": (i+1)*gap}
        task.init("github", val)

def init_repo_single_task(login):
    db = DMDatabase().getDB()
    task = DMTask()
    item = db["user"].find_one({"login": login})
    if item:
        val = {"name": "get_repos", "action_type": "single", "start": login, "end": item["id"]}
        task.init("github", val)
    else:
        print "user not found"
    
def test():
    task1 = DMTask()
    val = {"name": "fake-repo", "action_type": "loop", "start": 6001000, "end": 6005000}

    task1.init_test("github", val)
    e1 = GithubRepo(task1)
    e1.runTask()
    task1.remove()

def fix_add_count_id_created_at_int():
    db = DMDatabase().getDB()
#2730627
    gap = 1000
    start = 0
# end id is now set to 10300000
    end = 10300

    for i in range(start, end):
        res = db["user"].find({"id": {"$gte": i * gap, "$lt": (i+1)*gap}})
        for item in res:
#if it was the first run, no need to find it
            old_item = db["user_repos"].find_one({"login": item["login"]})
            if old_item:
                if old_item.has_key("created_at_int") and old_item.has_key("id") and old_item.has_key("count"):
                    continue
                else:
                    db["user_repos"].update({"login": item["login"]}, {"$set": {"created_at_int": item["created_at_int"], "id": item["id"], "count": item["public_repos"]}})
        print i

#this is a easy way to add a user_repos
def add_one():
    e1 = GithubRepo({})
    user_login = "tangqiwen"
    user_id = 4090768
    user_count = 32
    e1.upload_user_repos(user_login, user_id, user_count)

#add_one()

def fix_add_login_one_by_one():
    db = DMDatabase().getDB()
#2730627
    i = 0
    last_id = "<null>"
    while 1:
        res =db["user_repos"].find_one({"id": {"$exists": False}})
        if res:
            i += 1
            item = db["user"].find_one({"login": res["login"]})
            if item:
                if last_id == res["login"]:
                    print res["login"] +" dup, removed"
                    db["user_repos"].remove({"login": res["login"]})
                else:
                    print res["login"] + " updated "
                    created_at_int = 0
                    if item.has_key("created_at_int"):
                        created_at_int = item["created_at_int"]
                    else:
                        created_at_int = date_string_to_int(item["created_at"])
                    db["user_repos"].update({"login": res["login"]}, {"$set": {"created_at_int": created_at_int, "id": item["id"], "count": item["public_repos"]}})
                last_id = res["login"]
            else:
                print res["login"] + "  is not found"
                # if we get followers and not sync with user, this problem happens
                db["user_repos"].remove({"login": res["login"]})
        else:
            print 'exit'
            return
        if i%1000 == 0:
            print i

#test()
#init_repo_single_task("openstack")
#init_repo_task()
