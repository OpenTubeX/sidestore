const button = document.querySelector('#copy-source')
const status = document.querySelector('#copy-status')

button.addEventListener('click', async () => {
  const address = document.querySelector('#source-address').textContent
  try {
    await navigator.clipboard.writeText(address)
    status.textContent = 'Source URL copied.'
  } catch {
    const selection = window.getSelection()
    const range = document.createRange()
    range.selectNodeContents(document.querySelector('#source-address'))
    selection.removeAllRanges()
    selection.addRange(range)
    status.textContent = 'Select and copy the source URL above.'
  }
})

async function showReleases() {
  try {
    const response = await fetch('/source.json')
    if (!response.ok) return
    const source = await response.json()
    for (const app of source.apps) {
      const channel = app.bundleIdentifier.endsWith('.nightly') ? 'nightly' : 'stable'
      const version = app.versions[0]
      const target = document.querySelector(`#${channel}-version`)
      const link = document.createElement('a')
      link.href = version.downloadURL
      link.textContent = `Download IPA · ${version.version} (${version.buildVersion})`
      target.replaceChildren(link)
      if (channel === 'stable') {
        document.querySelector('#minimum-os').textContent = version.minOSVersion
      }
    }
  } catch {
    // Installation links and instructions remain usable without release metadata.
  }
}

showReleases()
