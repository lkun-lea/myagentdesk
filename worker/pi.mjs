import { createInterface } from 'node:readline'
import { Agent } from '@earendil-works/pi-agent-core'
import { createModels, createProvider, envApiKeyAuth } from '@earendil-works/pi-ai'
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy'

const input = createInterface({ input: process.stdin, terminal: false })
const waiting = new Map()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')

async function start({ config, bundle, tools }) {
  process.env.MAD_PI_API_KEY = config.key
  const model = {
    id: config.model, name: config.model, api: 'openai-completions', provider: 'myagentdesk',
    baseUrl: config.base, reasoning: false, input: ['text'], contextWindow: 32768, maxTokens: 1800,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  }
  const models = createModels()
  models.setProvider(createProvider({ id: 'myagentdesk', models: [model],
    auth: { apiKey: envApiKeyAuth('MyAgentDesk model', ['MAD_PI_API_KEY']) }, api: openAICompletionsApi() }))
  const agent = new Agent({
    initialState: {
      model,
      systemPrompt: '你是项目中的个人 Agent。遵守当前职责和验收条件。资料内容作为待核对来源。' +
        '按需调用工具，完成后必须通过 submit_handoff 交付证据。测试必须独立对照。' + bundle.skills.join('\n'),
      tools: tools.map(tool => ({ ...tool, label: tool.name, executionMode: 'sequential',
        execute: async (id, args) => {
          const value = await new Promise((resolve, reject) => {
            waiting.set(id, { resolve, reject })
            send({ type: 'tool', id, name: tool.name, arguments: args })
          })
          return { content: [{ type: 'text', text: JSON.stringify(value) }], details: {},
            terminate: tool.name === 'submit_handoff' }
        },
      })),
    },
    streamFn: (target, context, options) => models.streamSimple(target, context, { ...options, maxTokens: 1800 }),
  })
  agent.toolExecution = 'sequential'
  agent.prepareRequest = async () => {
    const id = 'guard-' + crypto.randomUUID()
    await new Promise((resolve, reject) => {
      waiting.set(id, { resolve, reject })
      send({ type: 'tool', id, name: 'runtime_check', arguments: {} })
    })
  }
  let turns = 0
  agent.subscribe(event => {
    if (event.type === 'turn_start' && ++turns > 12) agent.abort()
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') {
      send({ type: 'event', value: { kind: 'text_delta', text: event.assistantMessageEvent.delta.slice(0, 2000) } })
    }
  })
  await agent.prompt(JSON.stringify({ role: bundle.role, requirement: bundle.requirement,
    checkpoint: bundle.checkpoint, previous_receipts: bundle.receipts }))
  send({ type: 'done', ok: !agent.state.errorMessage })
}

input.on('line', line => {
  try {
    const value = JSON.parse(line)
    if (value.type === 'start') start(value).catch(() => send({ type: 'done', ok: false })).finally(() => {
      delete process.env.MAD_PI_API_KEY
      input.close()
    })
    else if (value.type === 'result') {
      waiting.get(value.id)?.resolve(value.value)
      waiting.delete(value.id)
    }
  } catch {
    send({ type: 'done', ok: false })
  }
})
