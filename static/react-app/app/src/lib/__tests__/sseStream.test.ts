import { describe, expect, it } from 'vitest'
import { parseSseChunk } from '../sseStream'

describe('parseSseChunk', () => {
  it('parses a complete start event with JSON data', () => {
    const buf = 'event: start\ndata: {"review_id":"r1","provider":"grok"}\n\n'
    const { events, rest } = parseSseChunk(buf)
    expect(rest).toBe('')
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('start')
    expect(events[0].data).toEqual({ review_id: 'r1', provider: 'grok' })
  })

  it('parses multiple token events', () => {
    const buf = 'event: token\ndata: {"text":"hel"}\n\nevent: token\ndata: {"text":"lo"}\n\n'
    const { events } = parseSseChunk(buf)
    expect(events).toHaveLength(2)
    expect((events[0].data as { text: string }).text).toBe('hel')
    expect((events[1].data as { text: string }).text).toBe('lo')
  })

  it('parses done event', () => {
    const buf = 'event: done\ndata: {"ok":true}\n\n'
    const { events } = parseSseChunk(buf)
    expect(events[0].event).toBe('done')
    expect((events[0].data as { ok: boolean }).ok).toBe(true)
  })

  it('keeps a trailing partial fragment as rest', () => {
    const buf = 'event: token\ndata: {"text":"hi"}\n\nevent: token\ndata: {"text":"par'
    const { events, rest } = parseSseChunk(buf)
    expect(events).toHaveLength(1)
    expect(rest).toBe('event: token\ndata: {"text":"par')
  })

  it('handles non-JSON data as raw string', () => {
    const buf = 'event: error\ndata: something plain\n\n'
    const { events } = parseSseChunk(buf)
    expect(events[0].event).toBe('error')
    expect(events[0].data).toBe('something plain')
  })

  it('ignores comment/unknown lines and defaults event to message', () => {
    const buf = ': this is a comment\ndata: {"x":1}\n\n'
    const { events } = parseSseChunk(buf)
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('message')
    expect(events[0].data).toEqual({ x: 1 })
  })

  it('returns no events for empty buffer', () => {
    const { events, rest } = parseSseChunk('')
    expect(events).toHaveLength(0)
    expect(rest).toBe('')
  })

  it('handles multi-line data fields joined by newline', () => {
    const buf = 'event: start\ndata: {"a":1\ndata: "b":2}\n\n'
    const { events } = parseSseChunk(buf)
    // The joined data "{\"a\":1\n\"b\":2}" is not valid JSON -> kept as raw string
    expect(events[0].event).toBe('start')
    expect(typeof events[0].data).toBe('string')
  })
})
