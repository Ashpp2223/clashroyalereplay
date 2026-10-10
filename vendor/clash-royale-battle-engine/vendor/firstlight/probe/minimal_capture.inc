// Local base-card replay profile. Reads native state directly; never builds rich JSON.
#ifdef CR_MINIMAL_CAPTURE
bool reject_minimal_capture(const char *reason) {
  LOGE("minimal capture rejected: %s", reason);
  return false;
}

bool install_minimal_capture_hook(std::uintptr_t base) {
  const NativeHookSpec hook = native_hook(kCombatConsumeCardOffset, kCombatConsumeCardPrologue,
      &combat_consume_card_hook, &g_original_combat_consume_card, "minimal card consumption");
  const bool installed = verify_combat_hook_fingerprints(base, &hook, 1) &&
                         install_native_hook_table(base, &hook, 1);
  // Reuse only the card-play producer and its epoch bookkeeping, not the full hook set.
  g_combat_hooks_enabled.store(installed, std::memory_order_release);
  return installed;
}

bool minimal_card_supported(int id) {
  constexpr int cards[] = {
    26000000, 26000001, 26000002, 26000003, 26000004, 26000005, 26000006, 26000007, 26000008, 26000009,
    26000010, 26000011, 26000012, 26000013, 26000014, 26000015, 26000016, 26000017, 26000018, 26000019,
    26000020, 26000021, 26000022, 26000023, 26000024, 26000025, 26000026, 26000027, 26000028, 26000029,
    26000030, 26000031, 26000032, 26000033, 26000034, 26000035, 26000036, 26000037, 26000038, 26000039,
    26000040, 26000041, 26000042, 26000043, 26000044, 26000045, 26000046, 26000047, 26000048, 26000049,
    26000050, 26000051, 26000052, 26000053, 26000054, 26000055, 26000056, 26000057, 26000058, 26000059,
    26000060, 26000061, 26000062, 26000063, 26000064, 26000065, 26000067, 26000068, 26000069, 26000072,
    26000074, 26000077, 26000080, 26000083, 26000084, 26000085, 26000087, 26000093, 26000095, 26000096,
    26000097, 26000099, 26000101, 26000102, 26000103, 26000106, 27000000, 27000001, 27000002, 27000003,
    27000004, 27000005, 27000006, 27000007, 27000008, 27000009, 27000010, 27000012, 27000013, 28000000,
    28000001, 28000002, 28000003, 28000004, 28000005, 28000006, 28000007, 28000008, 28000009, 28000010,
    28000011, 28000012, 28000013, 28000014, 28000015, 28000016, 28000017, 28000018, 28000023, 28000024,
    28000025, 28000026
  };
  for (int candidate : cards) if (candidate == id) return true;
  return false;
}

bool build_minimal_capture(char *buffer, std::size_t capacity, std::uint64_t generation,
                           std::uint64_t cursor, std::uint64_t *transmitted_next_out = nullptr) {
  const ObservationCaptureIdentity capture = observation_capture_identity_locked();
  void *manager = capture.manager;
  if (!manager || !capture.generation || !g_hook_install_ok.load() ||
      (generation && generation != capture.generation))
    return reject_minimal_capture("manager-or-generation");
  void *world = read_object_field<void *>(manager, 0xa8);
  void *object_manager = combat_object_manager_from_game_manager(manager);
  void **objects = nullptr;
  int count = 0;
  BattleResultView result;
  if (!world || !read_combat_object_vector(object_manager, &objects, &count) || count > 256 ||
      !read_battle_result(world, &result)) return reject_minimal_capture("world-or-object-vector");
  const int tick = g_game_state_tick(manager);
  std::size_t used = 0;
  JsonWriter json(buffer, capacity, used);
  json.begin_object();
  json.field("ok", true);
  json.field("schema", "cr-minimal.v3");
  json.field("generation", capture.generation);
  json.field("tick", tick);
  json.field("finalized", result.finalized);
  json.nullable("result", result.world_result_raw, result.finalized);
  json.append(",\"crowns\":[%d,%d]", result.crowns_raw[0], result.crowns_raw[1]);
  json.begin_array("players");
  for (int owner = 0; owner < 2; ++owner) {
    PlayerStateView player;
    if (!read_player_state(world, owner, &player) || player.deck_slots.count != 8)
      return reject_minimal_capture("player");
    for (int slot = 0; slot < 8; ++slot) {
      CardStateView card;
      if (!read_card_state(player, slot, false, &card) || !minimal_card_supported(card.card_id))
        return reject_minimal_capture("deck-card");
    }
    json.object_element();
    json.field("owner", owner);
    json.field("elixir", player.elixir_raw);
    json.begin_array("hand");
    auto *slots = static_cast<const std::int32_t *>(player.hand.data);
    bool first_hand = true;
    for (int i = 0; i < player.hand.count; ++i) {
      if (slots[i] < 0) continue;
      CardStateView card;
      if (!read_card_state(player, slots[i], true, &card)) return reject_minimal_capture("hand-card");
      json.append("%s[%d,%d,%d,%u]", first_hand ? "" : ",", i, card.card_id, card.cost, card.card_parameter);
      first_hand = false;
    }
    json.end_array();
    json.begin_array("cycle");
    auto *cycle = static_cast<const std::int32_t *>(player.cycle.data);
    for (int i = 0; i < player.cycle.count; ++i) {
      CardStateView card;
      if (!read_card_state(player, cycle[i], false, &card)) return reject_minimal_capture("cycle-card");
      json.element(card.card_id);
    }
    json.end_array();
    NativePlayerRuntimeView runtime;
    if (read_native_player_runtime(world, object_manager, objects, count, owner, &runtime)) {
      json.record("runtime", append_native_player_runtime_json, owner, runtime);
    } else {
      json.raw("runtime", "null");
    }
    json.end_object();
  }
  json.end_array();
  json.begin_array("entities");
  bool first = true;
  for (int i = 0; i < count; ++i) {
    const void *object = objects[i];
    if (!object) continue;
    NativeEntityIdentityView identity;
    if (!read_native_entity_identity(object, &identity) ||
        !native_object_id_is_unique_in_vector(object, objects, count, identity.native_object_id))
      return reject_minimal_capture("entity-identity");
    const int card_id = read_object_field<int>(object, 0xac);
    NativeProjectileStateView projectile;
    const NativeProjectileReadStatus projectile_status =
        read_native_projectile_state(object, objects, count, &projectile);
    if (projectile_status == NativeProjectileReadStatus::Invalid) return reject_minimal_capture("projectile");
    const bool is_projectile = projectile_status == NativeProjectileReadStatus::Valid;
    int hp = 0, max_hp = 0;
    const bool has_hp = read_hitpoints(object, &hp, &max_hp);
    const bool is_character = !is_projectile && has_hp;
    const char *entity_type = is_projectile ? "projectile" : !is_character ? "effect" : card_id == -1 ? "building" : "troop";
    int deployment_remaining_ms = 0;
    if (is_character) {
      constexpr std::size_t deploy_end = cr_phase_runtime::kObjectDeployRemainingOffset + sizeof(std::int32_t);
      if (!process_range_is_readable(object, deploy_end)) return reject_minimal_capture("deployment-readable");
      const int native_remaining =
          read_object_field<int>(object, cr_phase_runtime::kObjectDeployRemainingOffset);
      if (native_remaining < 0 || native_remaining > 3600000) return reject_minimal_capture("deployment-value");
      deployment_remaining_ms = ((native_remaining + 49) / 50) * 50;
    }
    NativeComponentInventoryView inventory;
    if (!read_native_component_inventory(object, &inventory)) inventory = {};
    const bool has_attack = inventory.count > 0 && inventory.slots[0].present &&
                            inventory.slots[0].owner_backlink_valid && inventory.slots[0].type_valid &&
                            inventory.slots[0].engine_type == 0;
    NativeTargetEntityView target;
    int attack_cooldown_ms = 0;
    if (has_attack) {
      if (!resolve_native_target_entity(object, inventory, objects, count, &target))
        return reject_minimal_capture("target");
      void **components = read_object_field<void **>(object, 0x18);
      const void *attack = components == nullptr ? nullptr : components[0];
      constexpr std::size_t cooldown_end = cr_phase_runtime::kAttackLoadRemainingOffset + sizeof(std::int32_t);
      if (!process_range_is_readable(attack, cooldown_end)) return reject_minimal_capture("cooldown-readable");
      const int native_remaining =
          read_object_field<int>(attack, cr_phase_runtime::kAttackLoadRemainingOffset);
      if (native_remaining < 0 || native_remaining > 3600000) return reject_minimal_capture("cooldown-value");
      attack_cooldown_ms = ((native_remaining + 49) / 50) * 50;
    }
    json.append("%s[%u,%d,%d,%d,%d,", first ? "" : ",", identity.native_object_id, identity.owner,
        card_id, read_object_field<int>(object, 0x7c),
        read_object_field<int>(object, 0x80));
    if (has_hp) json.append("%d,%d,", hp, max_hp); else json.append("null,null,");
    if (has_attack && target.has_target) json.append("%u,", target.native_object_id);
    else json.append("null,");
    if (has_attack) json.append("%d,", attack_cooldown_ms); else json.append("null,");
    json.append("\"%s\",", entity_type);
    if (is_character) json.append("%d]", deployment_remaining_ms); else json.append("null]");
    first = false;
  }
  json.end_array();
  const auto next = g_combat_next_sequence.load(std::memory_order_acquire);
  const auto epoch_first = g_combat_epoch_first_sequence.load(std::memory_order_acquire);
  const auto oldest = next - epoch_first > kCombatEventRingCapacity ? next - kCombatEventRingCapacity : epoch_first;
  if (!cursor) cursor = epoch_first;
  if (cursor < oldest || cursor > next || g_combat_rejected_capture_count.load() != 0)
    return reject_minimal_capture("event-cursor");
  json.begin_array("plays");
  first = true;
  auto transmitted_next = cursor;
  for (auto sequence = cursor; sequence < next; ++sequence) {
    const auto &slot = g_combat_event_ring[(sequence - 1) % kCombatEventRingCapacity];
    if (slot.published_sequence.load(std::memory_order_acquire) != sequence)
      return reject_minimal_capture("event-sequence");
    const auto &record = slot.record;
    if (record.generation != capture.generation || record.kind != CombatRawKind::CardPlay ||
        !record.deployment.validated) return reject_minimal_capture("event-record");
    // Consumption occurs during the transition into the next visible state.
    if (record.tick + 1 > tick) break;
    json.append("%s[%llu,%d,%d,%u,%d,%d]", first ? "" : ",", static_cast<unsigned long long>(sequence),
        record.tick + 1, record.deployment.owner, record.deployment.played_card_global_id,
        record.deployment.cost, record.deployment.form_code);
    first = false;
    transmitted_next = sequence + 1;
  }
  json.end_array();
  json.field("next", transmitted_next);
  json.end_object();
  if (transmitted_next_out != nullptr) *transmitted_next_out = transmitted_next;
  return json.good();
}

bool handle_minimal_transition(int socket, const char *command) {
  if (std::strncmp(command, "minimal-transition ", 19) != 0) return false;
  unsigned long long generation = 0, cursor_sequence = 0;
  unsigned int ticks = 0, action_count = 0;
  int consumed = 0;
  const char *cursor = command + 19;
  if (std::sscanf(cursor, "%llu %llu %u %u%n", &generation, &cursor_sequence, &ticks,
                  &action_count, &consumed) != 4 || ticks == 0 || ticks > 1000000 ||
      action_count > kMaxTransitionActions) {
    send_control_error(socket, "minimal-transition expects GENERATION CURSOR TICKS ACTION_COUNT");
    return true;
  }
  cursor += consumed;
  struct HandAction {
    int owner;
    int slot;
    int x;
    int y;
  } hand_actions[kMaxTransitionActions] = {};
  for (unsigned int index = 0; index < action_count; ++index) {
    consumed = 0;
    HandAction &action = hand_actions[index];
    if (std::sscanf(cursor, " %d %d %d %d%n", &action.owner, &action.slot,
                    &action.x, &action.y, &consumed) != 4 || consumed <= 0 ||
        action.owner < 0 || action.owner >= kPlayerCount) {
      send_control_error(socket, "invalid minimal-transition action");
      return true;
    }
    cursor += consumed;
  }
  while (*cursor == ' ' || *cursor == '\t') ++cursor;
  if (*cursor != '\0') {
    send_control_error(socket, "unexpected minimal-transition fields");
    return true;
  }

  DeployRequest actions[kMaxTransitionActions] = {};
  pthread_mutex_lock(&g_control_mutex);
  const bool current = g_controlled_manager && generation == g_control_generation;
  // t2 must equal the current boundary for execution in the following tick.
  const int command_tick = g_last_controlled_tick - static_cast<int>(kLiveCommandAgeTicks);
  bool resolved = current;
  for (unsigned int index = 0; resolved && index < action_count; ++index) {
    const HandAction &action = hand_actions[index];
    resolved = resolve_hand_deploy_request(g_controlled_manager, action.owner, action.slot,
                                           action.x, action.y, &actions[index]);
  }
  pthread_mutex_unlock(&g_control_mutex);
  if (!resolved) {
    send_control_error(socket, "could not resolve minimal-transition action");
    return true;
  }

  char receipt[kControlResponseBytes] = {};
  for (unsigned int index = 0; index < action_count; ++index) {
    char action_json[768] = {};
    if (!format_deploy_command(actions[index], command_tick, action_json, sizeof(action_json)) ||
        !queue_command_and_wait(action_json, receipt, sizeof(receipt))) {
      send_control_error(socket, "could not queue minimal-transition action");
      return true;
    }
  }
  const StepResult step = advance_controlled_manager(ticks);
  if (!step.completed) {
    send_control_error(socket, "minimal-transition did not complete");
    return true;
  }

  std::unique_ptr<char[]> buffer(new char[65536]());
  pthread_mutex_lock(&g_control_mutex);
  const bool captured = build_minimal_capture(buffer.get(), 65536, generation, cursor_sequence);
  pthread_mutex_unlock(&g_control_mutex);
  if (captured) send_control_response(socket, buffer.get());
  else send_control_error(socket, "minimal-transition capture failed");
  return true;
}

bool handle_minimal_capture(int socket, const char *command) {
  if (handle_minimal_transition(socket, command)) return true;
  if (std::strncmp(command, "minimal ", 8) != 0) return false;
  unsigned long long generation = 0, cursor = 0;
  char trailing = 0;
  if (std::sscanf(command + 8, "%llu %llu%c", &generation, &cursor, &trailing) != 2) {
    send_control_error(socket, "minimal expects GENERATION CURSOR");
    return true;
  }
  std::unique_ptr<char[]> buffer(new char[65536]());
  pthread_mutex_lock(&g_control_mutex);
  const bool ok = build_minimal_capture(buffer.get(), 65536, generation, cursor);
  pthread_mutex_unlock(&g_control_mutex);
  if (ok) send_control_response(socket, buffer.get());
  else send_control_error(socket, "minimal capture failed: unsupported state, stale cursor, or event loss");
  return true;
}
#endif
