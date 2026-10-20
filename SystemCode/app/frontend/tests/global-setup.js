// Builds the separate UAT database before the tests run: real backlog, three accounts, crew jobs.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

export default async function globalSetup() {
  const here = import.meta.dirname
  const root = path.resolve(here, '..', '..', '..')
  const dir = path.join(here, '.uat')
  fs.mkdirSync(dir, { recursive: true })
  const out = execFileSync('py', ['-W', 'ignore', 'app/scripts/uat_prepare.py', path.join(dir, 'accounts.json')], {
    cwd: root, env: { ...process.env, CIVICOPS_DB: path.join(dir, 'uat.db') }, encoding: 'utf-8',
  })
  console.log(out.trim().split('\n').pop())
}
