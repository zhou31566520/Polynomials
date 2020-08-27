package com.joyes.polynomials;

import java.util.Arrays;
import java.util.Objects;

public class Polynomials {

    /*
    × 多项式系数的数组
    * 例： 2X^3 + 3X^2 + 5
    * coefficient = [5,0,3,2]
    * */
    private Float[] coefficient ;

    // 多项式最高次数
    private int maxDegree ;

    public Polynomials(Float[] coefficient) {
        this.coefficient = coefficient;
    }

    @Override
    public String toString() {
        return "Polynomials{" +
                "coefficient=" + Arrays.toString(coefficient) +
                ", maxDegree=" + maxDegree +
                '}';
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        Polynomials that = (Polynomials) o;
        return maxDegree == that.maxDegree &&
                Arrays.equals(coefficient, that.coefficient);
    }

    @Override
    public int hashCode() {
        int result = Objects.hash(maxDegree);
        result = 31 * result + Arrays.hashCode(coefficient);
        return result;
    }

    public Float[] getCoefficient() {
        return coefficient;
    }

    public void setCoefficient(Float[] coefficient) {
        this.coefficient = coefficient;
    }

    public int getMaxDegree() {
        return maxDegree;
    }

    public void setMaxDegree(int maxDegree) {
        this.maxDegree = maxDegree;
    }
}
