<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const clock = ref('')
const batches = ref([])
const msg = ref('')
const err = ref('')
const result = ref(null)
const detail = ref(null)

async function refresh() {
  const c = await api.clock()
  clock.value = c.business_clock || ''
  batches.value = await api.batches()
}

async function saveClock() {
  msg.value = ''
  err.value = ''
  try {
    await api.setClock(clock.value)
    msg.value = '业务钟已更新'
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function applyDue() {
  msg.value = ''
  err.value = ''
  result.value = null
  detail.value = null
  try {
    result.value = await api.applyDue()
    msg.value = result.value.applied
      ? `批次 ${result.value.batch_no}：应用 ${result.value.ticket_count} 张改价单，写入 ${result.value.line_count} 行，被覆盖跳过 ${result.value.skipped_count} 行`
      : result.value.message
    await refresh()
  } catch (e) {
    err.value = e.message
  }
}

async function showDetail(batchId) {
  err.value = ''
  try {
    detail.value = detail.value && detail.value.id === batchId ? null : await api.batch(batchId)
  } catch (e) {
    err.value = e.message
  }
}

onMounted(() => refresh().catch((e) => (err.value = e.message)))
</script>

<template>
  <section class="hero">
    <h1>生效应用</h1>
    <p>拨动业务钟后，点「应用到期」将 effective_at ≤ 业务钟的改价单写入现行价签。同商品多张到期单时按「后写覆盖」：队序（生效时刻、单号）最后一单生效，前列记被覆盖跳过，批次明细可核。</p>
  </section>

  <div class="panel">
    <h2>业务钟</h2>
    <div class="row">
      <div class="field" style="flex:1">
        <label>业务钟 (UTC)</label>
        <input v-model="clock" />
      </div>
      <button class="btn ghost" type="button" @click="saveClock">保存业务钟</button>
      <button class="btn warn" type="button" @click="applyDue">应用到期</button>
    </div>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <pre v-if="result" style="margin-top:12px;font-size:0.82rem;color:var(--muted);white-space:pre-wrap">{{ JSON.stringify(result, null, 2) }}</pre>
  </div>

  <div class="panel">
    <h2>应用批次</h2>
    <table>
      <thead>
        <tr><th>批次</th><th>业务钟</th><th>改价单数</th><th>写入行</th><th>跳过行</th><th>策略</th><th>状态</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id" style="cursor:pointer" @click="showDetail(b.id)">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.clock_at }}</td>
          <td>{{ b.ticket_count }}</td>
          <td>{{ b.line_count }}</td>
          <td>{{ b.skipped_count }}</td>
          <td>{{ b.strategy }}</td>
          <td><span class="tag ok">{{ b.status }}</span></td>
          <td>{{ b.created_at }}</td>
        </tr>
      </tbody>
    </table>

    <template v-if="detail">
      <h2 style="margin-top:16px">批次 {{ detail.batch_no }} 明细</h2>
      <table>
        <thead>
          <tr><th>队序</th><th>改价单</th><th>商品</th><th>新价</th><th>行状态</th><th>被覆盖于</th></tr>
        </thead>
        <tbody>
          <tr v-for="l in detail.lines" :key="l.id">
            <td>{{ l.queue_pos }}</td>
            <td>{{ l.ticket_no }}</td>
            <td>{{ l.sku_code }}</td>
            <td>{{ l.new_price }}</td>
            <td>
              <span class="tag" :class="l.status === 'applied' ? 'ok' : 'muted'">{{ l.status }}</span>
            </td>
            <td>{{ l.covered_by_ticket_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>
