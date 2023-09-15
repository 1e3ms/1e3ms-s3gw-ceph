# Copyright 2022 SUSE, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import unittest
import sys
import boto3, botocore
import random
import string
import tempfile
import os
import filecmp
import threading


def _do_create_object(client, bucket_name, key, i):
    body = "data {i}".format(i=i)
    client.put_object(Bucket=bucket_name, Key=key, Body=body)


def _do_wait_completion(t):
    for thr in t:
        thr.join()


class VersioningSmokeTests(unittest.TestCase):
    ACCESS_KEY = "test"
    SECRET_KEY = "test"
    URL = "http://127.0.0.1:7480"
    BUCKET_NAME_LENGTH = 8
    OBJECT_NAME_LENGTH = 10

    def setUp(self):
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=VersioningSmokeTests.URL,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        self.s3 = boto3.resource(
            "s3",
            endpoint_url=VersioningSmokeTests.URL,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        self.test_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.s3_client.close()
        self.test_dir.cleanup()

    def get_random_name(self, length) -> str:
        letters = string.ascii_lowercase
        result_str = "".join(random.choice(letters) for i in range(length))
        return result_str

    def get_random_bucket_name(self) -> str:
        return self.get_random_name(VersioningSmokeTests.BUCKET_NAME_LENGTH)

    def get_random_object_name(self) -> str:
        return self.get_random_name(VersioningSmokeTests.OBJECT_NAME_LENGTH)

    def generate_random_file(self, path, size=4):
        # size passed is in mb
        size = size * 1024 * 1024
        with open(path, "wb") as fout:
            fout.write(os.urandom(size))

    def assert_bucket_exists(self, bucket_name):
        response = self.s3_client.list_buckets()
        found = False
        for bucket in response["Buckets"]:
            if bucket["Name"] == bucket_name:
                found = True
        self.assertTrue(found)

    def _do_create_versioned_obj_concurrent(self, bucket_name, key, num):
        t = []
        for i in range(num):
            thr = threading.Thread(
                target=_do_create_object, args=(self.s3_client, bucket_name, key, i)
            )
            thr.start()
            t.append(thr)
        return t

    def test_create_bucket_enable_versioning(self):
        bucket_name = self.get_random_bucket_name()
        self.s3_client.create_bucket(Bucket=bucket_name)
        self.assert_bucket_exists(bucket_name)
        # ensure versioning is disabled (default)
        response = self.s3_client.get_bucket_versioning(Bucket=bucket_name)
        self.assertEqual(response["ResponseMetadata"]["HTTPStatusCode"], 200)
        self.assertFalse("Status" in response)
        response = self.s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"MFADelete": "Disabled", "Status": "Enabled"},
        )
        response = self.s3_client.get_bucket_versioning(Bucket=bucket_name)
        self.assertTrue("Status" in response)
        self.assertEqual("Enabled", response["Status"])

    def test_put_objects_versioning_enabled(self):
        bucket_name = self.get_random_bucket_name()
        self.s3_client.create_bucket(Bucket=bucket_name)
        self.assert_bucket_exists(bucket_name)
        response = self.s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"MFADelete": "Disabled", "Status": "Enabled"},
        )
        object_name = self.get_random_object_name()
        test_file_path_1 = os.path.join(self.test_dir.name, "test_file_1.bin")
        self.generate_random_file(test_file_path_1)
        # upload the file
        self.s3_client.upload_file(test_file_path_1, bucket_name, object_name)

        # get the file and compare with the original
        test_file_path_1_check = os.path.join(
            self.test_dir.name, "test_file_1_check.bin"
        )
        self.s3_client.download_file(bucket_name, object_name, test_file_path_1_check)
        self.assertTrue(
            filecmp.cmp(test_file_path_1, test_file_path_1_check, shallow=False)
        )

        # now upload again with different content
        test_file_path_2 = os.path.join(self.test_dir.name, "test_file_2.bin")
        self.generate_random_file(test_file_path_2)
        self.s3_client.upload_file(test_file_path_2, bucket_name, object_name)
        test_file_path_2_check = os.path.join(
            self.test_dir.name, "test_file_2_check.bin"
        )
        self.s3_client.download_file(bucket_name, object_name, test_file_path_2_check)
        self.assertTrue(
            filecmp.cmp(test_file_path_2, test_file_path_2_check, shallow=False)
        )

        # get etag of object
        response = self.s3_client.head_object(Bucket=bucket_name, Key=object_name)
        self.assertTrue("ETag" in response)
        etag = response["ETag"]

        # check that we have 2 versions
        # only 1 version should be flagged as the latest
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix=object_name
        )
        self.assertTrue("Versions" in response)
        self.assertEqual(2, len(response["Versions"]))
        num_latest = 0
        last_version_id = ""
        previous_version_id = ""
        for version in response["Versions"]:
            self.assertEqual(os.path.getsize(test_file_path_1), version["Size"])
            self.assertEqual(object_name, version["Key"])
            self.assertEqual("STANDARD", version["StorageClass"])
            self.assertEqual(
                {"DisplayName": "M. Tester", "ID": "testid"}, version["Owner"]
            )
            self.assertNotEqual("null", version["VersionId"])
            if version["IsLatest"]:
                num_latest += 1
                last_version_id = version["VersionId"]
                self.assertEqual(etag, version["ETag"])
            else:
                previous_version_id = version["VersionId"]

        # check that all etags differ
        for version in response["Versions"]:
            etag = version["ETag"]
            version_id = version["VersionId"]
            for version2 in response["Versions"]:
                version_id2 = version2["VersionId"]
                if version_id2 != version_id:
                    etag2 = version2["ETag"]
                    self.assertNotEqual(etag, etag2)

        self.assertEqual(1, num_latest)
        self.assertNotEqual("", last_version_id)
        self.assertNotEqual("", previous_version_id)

        # download by version_id
        # download the last version
        check_version_file = os.path.join(self.test_dir.name, "check_version.bin")
        bucket = self.s3.Bucket(bucket_name)
        bucket.download_file(
            object_name, check_version_file, ExtraArgs={"VersionId": last_version_id}
        )
        self.assertTrue(
            filecmp.cmp(test_file_path_2, check_version_file, shallow=False)
        )

        # download the previous version
        check_version_file_2 = os.path.join(self.test_dir.name, "check_version2.bin")
        bucket.download_file(
            object_name,
            check_version_file_2,
            ExtraArgs={"VersionId": previous_version_id},
        )
        self.assertTrue(
            filecmp.cmp(test_file_path_1, check_version_file_2, shallow=False)
        )

        # delete the object
        self.s3_client.delete_object(Bucket=bucket_name, Key=object_name)

        # check that we have 2 versions plus 1 DeleteMarker
        # only 1 version should be flagged as the latest
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix=object_name
        )
        self.assertTrue("Versions" in response)
        self.assertEqual(2, len(response["Versions"]))

        num_latest = 0
        deleted_version_id = ""
        for version in response["Versions"]:
            self.assertEqual(os.path.getsize(test_file_path_1), version["Size"])
            self.assertEqual(object_name, version["Key"])
            self.assertEqual("STANDARD", version["StorageClass"])
            self.assertEqual(
                {"DisplayName": "M. Tester", "ID": "testid"}, version["Owner"]
            )
            self.assertNotEqual("null", version["VersionId"])
            self.assertFalse(version["IsLatest"])

        self.assertEqual(1, len(response["DeleteMarkers"]))

        # try to download the file, a 404 error should be returned
        check_deleted_file = os.path.join(self.test_dir.name, "check_deleted.bin")
        with self.assertRaises(botocore.exceptions.ClientError) as context:
            response = self.s3_client.download_file(
                bucket_name, object_name, check_deleted_file
            )
        self.assertTrue("404" in str(context.exception))

        # download the previous version, it should still be reacheable
        check_version_file_2 = os.path.join(self.test_dir.name, "check_version2.bin")
        bucket.download_file(
            object_name, check_version_file_2, ExtraArgs={"VersionId": last_version_id}
        )
        self.assertTrue(
            filecmp.cmp(test_file_path_2, check_version_file_2, shallow=False)
        )

        # delete the first version. (in this case version should be deleted
        # permanently)
        version_id_to_delete = response["Versions"][0]["VersionId"]
        # delete the specific version
        self.s3_client.delete_object(
            Bucket=bucket_name, Key=object_name, VersionId=version_id_to_delete
        )
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix=object_name
        )
        self.assertTrue("Versions" in response)
        self.assertEqual(1, len(response["Versions"]))
        self.assertNotEqual(version_id_to_delete, response["Versions"][0]["VersionId"])
        self.assertTrue("DeleteMarkers" in response)
        self.assertEqual(1, len(response["DeleteMarkers"]))

    def test_put_objects_no_versioning(self):
        bucket_name = self.get_random_bucket_name()
        self.s3_client.create_bucket(Bucket=bucket_name)
        self.assert_bucket_exists(bucket_name)
        object_name = self.get_random_object_name()
        test_file_path_1 = os.path.join(self.test_dir.name, "test_file_1.bin")
        self.generate_random_file(test_file_path_1)
        # upload the file
        self.s3_client.upload_file(test_file_path_1, bucket_name, object_name)

        # get the file and compare with the original
        test_file_path_1_check = os.path.join(
            self.test_dir.name, "test_file_1_check.bin"
        )
        self.s3_client.download_file(bucket_name, object_name, test_file_path_1_check)
        self.assertTrue(
            filecmp.cmp(test_file_path_1, test_file_path_1_check, shallow=False)
        )

        # now upload again with different content
        test_file_path_2 = os.path.join(self.test_dir.name, "test_file_2.bin")
        self.generate_random_file(test_file_path_2)
        self.s3_client.upload_file(test_file_path_2, bucket_name, object_name)
        test_file_path_2_check = os.path.join(
            self.test_dir.name, "test_file_2_check.bin"
        )
        self.s3_client.download_file(bucket_name, object_name, test_file_path_2_check)
        self.assertTrue(
            filecmp.cmp(test_file_path_2, test_file_path_2_check, shallow=False)
        )

        # get etag of object
        response = self.s3_client.head_object(Bucket=bucket_name, Key=object_name)
        self.assertTrue("ETag" in response)
        etag = response["ETag"]

        # check that we have 1 version only
        # only 1 version should be flagged as the latest
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix=object_name
        )
        self.assertTrue("Versions" in response)
        self.assertEqual(1, len(response["Versions"]))
        num_latest = 0
        last_version_id = ""
        previous_version_id = ""
        for version in response["Versions"]:
            self.assertEqual(os.path.getsize(test_file_path_1), version["Size"])
            self.assertEqual(object_name, version["Key"])
            self.assertEqual("STANDARD", version["StorageClass"])
            self.assertEqual(
                {"DisplayName": "M. Tester", "ID": "testid"}, version["Owner"]
            )
            self.assertEqual(etag, version["ETag"])
            self.assertEqual("null", version["VersionId"])
            self.assertTrue(version["IsLatest"])

        # delete the object
        self.s3_client.delete_object(Bucket=bucket_name, Key=object_name)

        # we should still have 0 versions and no delete markers
        # non-versioned bucket don't create delete-markers
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix=object_name
        )
        self.assertFalse("DeleteMarkers" in response)
        self.assertFalse("Versions" in response)

        # try to download the file, a 404 error should be returned
        check_deleted_file = os.path.join(self.test_dir.name, "check_deleted.bin")
        with self.assertRaises(botocore.exceptions.ClientError) as context:
            response = self.s3_client.download_file(
                bucket_name, object_name, check_deleted_file
            )
        self.assertTrue("404" in str(context.exception))

    def upload_object_with_versions(self, bucket_name, object_name, number_of_versions):
        for i in range(number_of_versions):
            test_file_path_1 = os.path.join(self.test_dir.name, object_name + "%s" % i)
            self.generate_random_file(test_file_path_1)
            # upload the file
            self.s3_client.upload_file(test_file_path_1, bucket_name, object_name)

    def test_list_objects_versioning_enabled_with_prefix(self):
        bucket_name = self.get_random_bucket_name()
        self.s3_client.create_bucket(Bucket=bucket_name)
        self.assert_bucket_exists(bucket_name)
        response = self.s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"MFADelete": "Disabled", "Status": "Enabled"},
        )

        self.upload_object_with_versions(bucket_name, "prefix_file_1.bin", 2)
        self.upload_object_with_versions(bucket_name, "prefix_file_2.bin", 2)
        self.upload_object_with_versions(bucket_name, "test_file.bin", 3)

        # get the list of version with prefix = 'prefix'
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix="prefix"
        )
        self.assertTrue("Versions" in response)
        # we should have 4 versions (2 per each file)
        self.assertEqual(4, len(response["Versions"]))
        # check that the results are the expected ones
        for version in response["Versions"]:
            self.assertTrue(version["Key"].startswith("prefix"))

        # get the list of version with prefix = 'test'
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix="test"
        )
        self.assertTrue("Versions" in response)
        # we should have 3 versions
        self.assertEqual(3, len(response["Versions"]))
        # check that the results are the expected ones
        for version in response["Versions"]:
            self.assertTrue(version["Key"].startswith("test"))

        # delete the prefix_file_1.bin object
        self.s3_client.delete_object(Bucket=bucket_name, Key="prefix_file_1.bin")
        # get the list of version with prefix = 'prefix'
        response = self.s3_client.list_object_versions(
            Bucket=bucket_name, Prefix="prefix"
        )
        self.assertTrue("Versions" in response)
        # we should have still have 4 versions (2 per each file)
        self.assertEqual(4, len(response["Versions"]))

        # and we should have 1 delete marker
        self.assertTrue("DeleteMarkers" in response)
        self.assertEqual(1, len(response["DeleteMarkers"]))
        # ensure that it's object we deleted
        self.assertEqual("prefix_file_1.bin", response["DeleteMarkers"][0]["Key"])

    def test_create_concurrent(self):
        bucket_name = self.get_random_bucket_name()
        self.s3_client.create_bucket(Bucket=bucket_name)
        self.assert_bucket_exists(bucket_name)
        # ensure versioning is disabled (default)
        response = self.s3_client.get_bucket_versioning(Bucket=bucket_name)
        self.assertEqual(response["ResponseMetadata"]["HTTPStatusCode"], 200)
        self.assertFalse("Status" in response)
        response = self.s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"MFADelete": "Disabled", "Status": "Enabled"},
        )
        response = self.s3_client.get_bucket_versioning(Bucket=bucket_name)
        self.assertTrue("Status" in response)
        self.assertEqual("Enabled", response["Status"])

        key = "myobj"
        num_versions = 5
        repeat = 25

        for i in range(repeat):
            key_obj = "%s-%s" % (key, i)
            t = self._do_create_versioned_obj_concurrent(
                bucket_name, key_obj, num_versions
            )
            _do_wait_completion(t)
        response = self.s3_client.list_object_versions(Bucket=bucket_name)
        versions = response["Versions"]
        self.assertEqual(num_versions * repeat, len(versions))
        print("Num versions: %s" % len(versions))


if __name__ == "__main__":
    if len(sys.argv) == 2:
        address_port = sys.argv.pop()
        VersioningSmokeTests.URL = "http://{0}".format(address_port)
        unittest.main()
    else:
        print("usage: {0} ADDRESS:PORT".format(sys.argv[0]))
