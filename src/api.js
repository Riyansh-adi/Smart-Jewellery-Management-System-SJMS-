const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'
export function token(){ return localStorage.getItem('sjms_token') }
export async function request(path, options={}) {
  const headers = { ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...(token() ? {Authorization:`Bearer ${token()}`} : {}), ...options.headers }
  const response = await fetch(`${API}${path}`, { ...options, headers })
  if (!response.ok) { let message = `Request failed (${response.status})`; try { const data=await response.json(); message=data.detail || message } catch (err) { message=err.message || message } throw new Error(message) }
  if (response.headers.get('content-type')?.includes('text/csv')) return response.blob()
  return response.status===204 ? null : response.json()
}
export const api = {
  login: async (email,password) => { const body=new URLSearchParams({username:email,password}); const data=await request('/auth/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body}); localStorage.setItem('sjms_token',data.access_token); return data.user },
  me:()=>request('/auth/me'), dashboard:()=>request('/dashboard'),
  list:(key,search='')=>request(`/${key}${search?`?search=${encodeURIComponent(search)}`:''}`),
  create:(key,data)=>request(`/${key}`,{method:'POST',body:JSON.stringify(data)}),
  update:(key,id,data)=>request(`/${key}/${id}`,{method:'PATCH',body:JSON.stringify(data)}),
  settings:()=>request('/settings'), saveSettings:(data)=>request('/settings',{method:'PUT',body:JSON.stringify(data)}),
  report:(start,end)=>request(`/reports/summary${start||end?`?${new URLSearchParams({...(start?{start}:{}),...(end?{end}:{})})}`:''}`),
  csv:(start,end)=>request(`/reports/sales.csv${start||end?`?${new URLSearchParams({...(start?{start}:{}),...(end?{end}:{})})}`:''}`),
  downloadInvoice:(id)=>request(`/sales/${id}`),
}
