// -*- mode:C++; tab-width:8; c-basic-offset:2; indent-tabs-mode:t -*-
// vim: ts=2 sw=2 expandtab ft=cpp

/*
 * Ceph - scalable distributed file system
 *
 * Simple filesystem SAL implementation
 *
 * Copyright (C) 2021
 *
 * This is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License version 2.1, as published by the Free Software
 * Foundation. See file COPYING.
 *
 */
#include <filesystem>

#include "rgw_multi.h"
#include "rgw_notify.h"
#include "rgw_oidc_provider.h"
#include "rgw_putobj_processor.h"
#include "rgw_rados.h"
#include "rgw_role.h"
#include "rgw_sal.h"
#include "rgw_sal_dbstore.h"

#include "store/simplefile/user.h"
#include "store/simplefile/bucket.h"
#include "store/simplefile/object.h"
#include "store/simplefile/zone.h"

namespace rgw::sal {

class SimpleFileStore;

class UnsupportedLuaScriptManager : public LuaScriptManager {
 public:
  UnsupportedLuaScriptManager() = default;
  UnsupportedLuaScriptManager(const UnsupportedLuaScriptManager&) = delete;
  UnsupportedLuaScriptManager& operator=(const UnsupportedLuaScriptManager&) = delete;
  virtual ~UnsupportedLuaScriptManager() = default;

  virtual int get(const DoutPrefixProvider *dpp, optional_yield y,
                  const std::string &key, std::string &script) override {
    return -ENOENT;
  }
  virtual int put(const DoutPrefixProvider *dpp, optional_yield y,
                  const std::string &key, const std::string &script) override {
    return -ENOENT;
  }
  virtual int del(const DoutPrefixProvider *dpp, optional_yield y,
                  const std::string &key) override {
    return -ENOENT;
  }
};

class SimpleFileStore : public Store {
 private:
  RGWUserInfo dummy_user;
  RGWSyncModuleInstanceRef sync_module;
  SimpleFileZone zone;
  const std::filesystem::path data_path;
  std::string luarocks_path = "";
  CephContext *const cctx;

 public:
  SimpleFileStore(CephContext *c, const std::filesystem::path &data_path);
  SimpleFileStore(const SimpleFileStore&) = delete;
  SimpleFileStore& operator=(const SimpleFileStore&) = delete;
  ~SimpleFileStore();

  virtual int initialize(
    CephContext* cct,
    const DoutPrefixProvider* dpp
  ) override;
  virtual void finalize(void) override;
  void maybe_init_store();

  virtual const std::string get_name() const override { return "simplefile"; }

  virtual std::string get_cluster_id(const DoutPrefixProvider *dpp,
                                     optional_yield y) override {
    return "NA";
  }
  virtual bool is_meta_master() override { return true; }
  virtual std::unique_ptr<Object> get_object(const rgw_obj_key &k) {
    // ldout(ctx(), 10) << __func__ << ": TODO obj_key=" << k << dendl;
    return std::make_unique<SimpleFileObject>(*this, k);
  }
  virtual RGWCoroutinesManagerRegistry *get_cr_registry() override {
    return nullptr;
  }
  virtual CephContext *ctx(void) override { return cctx; };
  CephContext *ceph_context() const { return cctx; };

  virtual Zone *get_zone() override { return &zone; }
  virtual const std::string &get_luarocks_path() const override {
    return luarocks_path;
  }
  virtual void set_luarocks_path(const std::string &path) override {
    luarocks_path = path;
  }
  virtual std::unique_ptr<User> get_user(const rgw_user &u) override;
  virtual int get_user_by_access_key(const DoutPrefixProvider *dpp,
                                     const std::string &key, optional_yield y,
                                     std::unique_ptr<User> *user) override;
  virtual int get_user_by_email(const DoutPrefixProvider *dpp,
                                const std::string &email, optional_yield y,
                                std::unique_ptr<User> *user) override;
  virtual int get_user_by_swift(const DoutPrefixProvider *dpp,
                                const std::string &user_str, optional_yield y,
                                std::unique_ptr<User> *user) override;
  virtual int get_bucket(User *u, const RGWBucketInfo &i,
                         std::unique_ptr<Bucket> *bucket) override;
  virtual int get_bucket(const DoutPrefixProvider *dpp, User *u,
                         const rgw_bucket &b, std::unique_ptr<Bucket> *bucket,
                         optional_yield y) override;
  virtual int get_bucket(const DoutPrefixProvider *dpp, User *u,
                         const std::string &tenant, const std::string &name,
                         std::unique_ptr<Bucket> *bucket,
                         optional_yield y) override;
  virtual int forward_request_to_master(const DoutPrefixProvider *dpp,
                                        User *user, obj_version *objv,
                                        bufferlist &in_data, JSONParser *jp,
                                        req_info &info,
                                        optional_yield y) override;
  virtual int forward_iam_request_to_master(
    const DoutPrefixProvider* dpp,
    const RGWAccessKey& key,
    obj_version* objv,
    bufferlist& in_data,
    RGWXMLDecoder::XMLParser* parser,
    req_info& info,
    optional_yield y
  ) override;
  virtual std::string zone_unique_id(uint64_t unique_num) override;
  virtual std::string zone_unique_trans_id(const uint64_t unique_num) override;
  virtual int cluster_stat(RGWClusterStat &stats) override;
  virtual std::unique_ptr<Lifecycle> get_lifecycle(void) override;
  virtual RGWLC *get_rgwlc(void) override;
  virtual std::unique_ptr<Completions> get_completions(void) override;
  virtual std::unique_ptr<Notification> get_notification(
      rgw::sal::Object *obj, rgw::sal::Object *src_obj, struct req_state *s,
      rgw::notify::EventType event_type,
      const std::string *object_name = nullptr) override;
  virtual std::unique_ptr<Notification> get_notification(
      const DoutPrefixProvider *dpp, rgw::sal::Object *obj,
      rgw::sal::Object *src_obj, rgw::notify::EventType event_type,
      rgw::sal::Bucket *_bucket, std::string &_user_id,
      std::string &_user_tenant, std::string &_req_id,
      optional_yield y) override;

  /** Log usage data to the store.  Usage data is things like bytes
   * sent/received and op count */
  virtual int log_usage(
      const DoutPrefixProvider *dpp,
      std::map<rgw_user_bucket, RGWUsageBatch> &usage_info) override;

  /** Log OP data to the store.  Data is opaque to SAL */
  virtual int log_op(const DoutPrefixProvider *dpp, std::string &oid,
                     bufferlist &bl) override;

  virtual int register_to_service_map(
      const DoutPrefixProvider *dpp, const std::string &daemon_type,
      const std::map<std::string, std::string> &meta) override;

  virtual void get_quota(RGWQuota& quota) override;

  virtual void get_ratelimit(RGWRateLimitInfo &bucket_ratelimit,
                             RGWRateLimitInfo &user_ratelimit,
                             RGWRateLimitInfo &anon_ratelimit) override;

  virtual int set_buckets_enabled(const DoutPrefixProvider *dpp,
                                  std::vector<rgw_bucket> &buckets,
                                  bool enabled) override;

  virtual uint64_t get_new_req_id() override { return 0; }
  virtual int get_sync_policy_handler(const DoutPrefixProvider *dpp,
                                      std::optional<rgw_zone_id> zone,
                                      std::optional<rgw_bucket> bucket,
                                      RGWBucketSyncPolicyHandlerRef *phandler,
                                      optional_yield y) override;

  /** Get a status manager for bucket sync */
  virtual RGWDataSyncStatusManager *get_data_sync_manager(
      const rgw_zone_id &source_zone) override;

  virtual void wakeup_meta_sync_shards(std::set<int> &shard_ids) override;
  virtual void wakeup_data_sync_shards(
    const DoutPrefixProvider* dpp,
    const rgw_zone_id& source_zone,
    boost::container::flat_map<
      int,
      boost::container::flat_set<rgw_data_notify_entry>
    >& shard_ids
  ) override;
  virtual int clear_usage(const DoutPrefixProvider *dpp) override { return 0; }
  virtual int read_all_usage(
      const DoutPrefixProvider *dpp, uint64_t start_epoch, uint64_t end_epoch,
      uint32_t max_entries, bool *is_truncated, RGWUsageIter &usage_iter,
      std::map<rgw_user_bucket, rgw_usage_log_entry> &usage) override;
  virtual int trim_all_usage(const DoutPrefixProvider *dpp,
                             uint64_t start_epoch, uint64_t end_epoch) override;
  virtual int get_config_key_val(std::string name, bufferlist *bl) override;
  virtual int meta_list_keys_init(const DoutPrefixProvider *dpp,
                                  const std::string &section,
                                  const std::string &marker,
                                  void **phandle) override;
  virtual int meta_list_keys_next(const DoutPrefixProvider *dpp, void *handle,
                                  int max, std::list<std::string> &keys,
                                  bool *truncated) override;
  virtual void meta_list_keys_complete(void *handle) override;
  virtual std::string meta_get_marker(void *handle) override;
  virtual int meta_remove(const DoutPrefixProvider *dpp,
                          std::string &metadata_key, optional_yield y) override;

  virtual const RGWSyncModuleInstanceRef &get_sync_module() override;

  virtual std::string get_host_id() override;

  virtual std::unique_ptr<LuaScriptManager> get_lua_script_manager() override;

  virtual std::unique_ptr<RGWRole> get_role(
    std::string name,
    std::string tenant,
    std::string path = "",
    std::string trust_policy = "",
    std::string max_session_duration_str = "",
    std::multimap<std::string, std::string> tags = {}
  ) override;

  virtual std::unique_ptr<RGWRole> get_role(std::string id) override;
  virtual std::unique_ptr<RGWRole> get_role(const RGWRoleInfo& info) override;
  virtual int get_roles(
    const DoutPrefixProvider *dpp,
    optional_yield y,
    const std::string &path_prefix,
    const std::string &tenant,
    std::vector<std::unique_ptr<RGWRole>> &roles
  ) override;

  virtual std::unique_ptr<RGWOIDCProvider> get_oidc_provider() override;

  virtual int get_oidc_providers(
      const DoutPrefixProvider *dpp, const std::string &tenant,
      std::vector<std::unique_ptr<RGWOIDCProvider>> &providers) override {
    return 0;
  }

  virtual std::unique_ptr<Writer> get_append_writer(
      const DoutPrefixProvider *dpp, optional_yield y,
      std::unique_ptr<rgw::sal::Object> _head_obj, const rgw_user &owner,
      const rgw_placement_rule *ptail_placement_rule,
      const std::string &unique_tag, uint64_t position,
      uint64_t *cur_accounted_size) override;

  virtual std::unique_ptr<Writer> get_atomic_writer(
      const DoutPrefixProvider *dpp, optional_yield y,
      std::unique_ptr<rgw::sal::Object> _head_obj, const rgw_user &owner,
      const rgw_placement_rule *ptail_placement_rule, uint64_t olh_epoch,
      const std::string &unique_tag) override;

  virtual const std::string& get_compression_type(
    const rgw_placement_rule& rule
  ) override {
    return zone.get_params().get_compression_type(rule);
  }
  virtual bool valid_placement(const rgw_placement_rule& rule) override {
    return zone.get_params().valid_placement(rule);
  }

  // TODO make proper bucket path
  std::filesystem::path meta_path() const;
  std::filesystem::path buckets_path() const;
  std::filesystem::path users_path() const;
  std::filesystem::path bucket_path(const rgw_bucket &bucket) const;
  std::filesystem::path bucket_metadata_path(
      const rgw_bucket &bucket, const std::string &metadata_fn) const;
  std::filesystem::path objects_path(const rgw_bucket &bucket) const;
  std::filesystem::path object_path(const rgw_bucket &bucket,
                                    const rgw_obj_key &obj) const;
  std::filesystem::path object_data_path(const rgw_bucket &bucket,
                                         const rgw_obj_key &obj) const;
  std::filesystem::path object_metadata_path(
      const rgw_bucket &bucket, const rgw_obj_key &obj,
      const std::string &metadata_fn) const;
};

}  // namespace rgw::sal