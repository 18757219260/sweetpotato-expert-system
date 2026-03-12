// miniprogram/pages/farm/farm.js - 农场档案页面
const { API_BASE } = require('../../config')
const { getProvinces, getCities, getDistricts } = require('../../utils/regions')

Page({
  data: {
    // 省市区选择器
    provinces: [],
    cities: [],
    districts: [],
    provinceIndex: 0,
    cityIndex: 0,
    districtIndex: 0,

    // 土壤类型选择器
    soilTypes: ['沙土', '壤土', '黏土', '沙壤土', '黏壤土', '其他'],
    soilTypeIndex: 0,

    // 表单数据
    area_mu: '',
    other_info: '',

    // 加载状态
    loading: false,
    hasProfile: false
  },

  onLoad() {
    this.setData({
      provinces: getProvinces()
    })
    this._loadProfile()
  },

  // ── 加载已有档案 ────────────────────────────────────────────────────────────
  _loadProfile() {
    const token = wx.getStorageSync('token')
    if (!token) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      setTimeout(() => wx.navigateBack(), 1500)
      return
    }

    wx.request({
      url: `${API_BASE}/api/farm/profile`,
      method: 'GET',
      header: { Authorization: `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode === 200) {
          const profile = res.data
          const provinceIndex = this.data.provinces.indexOf(profile.province)
          const cities = getCities(profile.province)
          const cityIndex = cities.indexOf(profile.city)
          const districts = getDistricts(profile.city)
          const districtIndex = districts.indexOf(profile.district)
          const soilTypeIndex = profile.soil_type ? this.data.soilTypes.indexOf(profile.soil_type) : 0

          this.setData({
            hasProfile: true,
            provinceIndex: provinceIndex >= 0 ? provinceIndex : 0,
            cities,
            cityIndex: cityIndex >= 0 ? cityIndex : 0,
            districts,
            districtIndex: districtIndex >= 0 ? districtIndex : 0,
            area_mu: profile.area_mu ? String(profile.area_mu) : '',
            soilTypeIndex: soilTypeIndex >= 0 ? soilTypeIndex : 0,
            other_info: profile.other_info || ''
          })
        } else if (res.statusCode === 404) {
          // 没有档案，初始化默认值
          const cities = getCities(this.data.provinces[0])
          const districts = getDistricts(cities[0])
          this.setData({ cities, districts })
        }
      },
      fail: (err) => {
        console.error('[farm] load profile fail:', err)
      }
    })
  },

  // ── 省份选择 ────────────────────────────────────────────────────────────────
  onProvinceChange(e) {
    const provinceIndex = parseInt(e.detail.value)
    const province = this.data.provinces[provinceIndex]
    const cities = getCities(province)
    const districts = getDistricts(cities[0])

    this.setData({
      provinceIndex,
      cities,
      cityIndex: 0,
      districts,
      districtIndex: 0
    })
  },

  // ── 城市选择 ────────────────────────────────────────────────────────────────
  onCityChange(e) {
    const cityIndex = parseInt(e.detail.value)
    const city = this.data.cities[cityIndex]
    const districts = getDistricts(city)

    this.setData({
      cityIndex,
      districts,
      districtIndex: 0
    })
  },

  // ── 区县选择 ────────────────────────────────────────────────────────────────
  onDistrictChange(e) {
    this.setData({
      districtIndex: parseInt(e.detail.value)
    })
  },

  // ── 土壤类型选择 ────────────────────────────────────────────────────────────
  onSoilTypeChange(e) {
    this.setData({
      soilTypeIndex: parseInt(e.detail.value)
    })
  },

  // ── 种植面积输入 ────────────────────────────────────────────────────────────
  onAreaInput(e) {
    this.setData({
      area_mu: e.detail.value
    })
  },

  // ── 其他信息输入 ────────────────────────────────────────────────────────────
  onOtherInfoInput(e) {
    this.setData({
      other_info: e.detail.value
    })
  },

  // ── 保存档案 ────────────────────────────────────────────────────────────────
  onSave() {
    const { provinces, cities, districts, provinceIndex, cityIndex, districtIndex, soilTypes, soilTypeIndex, area_mu, other_info } = this.data

    const province = provinces[provinceIndex]
    const city = cities[cityIndex]
    const district = districts[districtIndex]
    const soil_type = soilTypes[soilTypeIndex]

    if (!province || !city || !district) {
      wx.showToast({ title: '请选择完整地址', icon: 'none' })
      return
    }

    const token = wx.getStorageSync('token')
    if (!token) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }

    this.setData({ loading: true })

    wx.request({
      url: `${API_BASE}/api/farm/profile`,
      method: 'POST',
      header: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      data: {
        province,
        city,
        district,
        area_mu: area_mu ? parseFloat(area_mu) : null,
        soil_type,
        other_info: other_info || null
      },
      success: (res) => {
        this.setData({ loading: false })
        if (res.statusCode === 200) {
          wx.showToast({ title: '保存成功', icon: 'success' })
          setTimeout(() => wx.navigateBack(), 1500)
        } else {
          wx.showToast({ title: res.data.detail || '保存失败', icon: 'none' })
        }
      },
      fail: (err) => {
        this.setData({ loading: false })
        console.error('[farm] save fail:', err)
        wx.showToast({ title: '网络错误', icon: 'none' })
      }
    })
  }
})
