package com.joyes.polynomials;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.InputStream;

public class PolynomialsUtils {

    public static Polynomials maxPolynomials(Polynomials a ,Polynomials b ) {
        if (a.getMaxDegree() > b.getMaxDegree()){
            return a ;
        }else if ( a.getMaxDegree() < b.getMaxDegree()){
            return b  ;
        }else {
            return a;
        }
    }

    public static Polynomials minPolynomials(Polynomials a , Polynomials b ){
        if (a.getMaxDegree() > b.getMaxDegree()){
            return b ;
        }else if ( a.getMaxDegree() < b.getMaxDegree()){
            return a  ;
        }else {
            return b;
        }
    }

    public static Float[] addPolynomials(Polynomials a, Polynomials b ){
        Float[] l = maxPolynomials(a,b).getCoefficient();
        Float[] m = minPolynomials(a,b).getCoefficient();
        Float[] c = new Float[l.length];
        for(int i = 0 ; i < l.length ; i ++ ){
            if (i < m.length) {
                c[i] = l[i] + m[i];
            }else{
                c[i] = l[i];
            }
        }
        return c;
    }
    public static Float[] jianPolynomials(Polynomials a , Polynomials b ) {
        Float[] c = new Float[maxPolynomials(a,b).getCoefficient().length];
        if (a.equals(maxPolynomials(a,b))){
            for ( int i = 0 ; i < c.length ; i++){
                if ( i < b.getCoefficient().length){
                    c[i] = a.getCoefficient()[i] - b.getCoefficient()[i];
                }else{
                    c[i] = a.getCoefficient()[i];
                }
            }
        }else{
            for ( int i = 0 ; i < c.length ; i++){
                if ( i < a.getCoefficient().length){
                    c[i] = a.getCoefficient()[i] - b.getCoefficient()[i];
                }else{
                    c[i] = - b.getCoefficient()[i];
                }
            }
        }
        return c ;
    }

    public static void readFile(String filePath) throws FileNotFoundException {
        File file = new File(filePath);
        InputStream inputStream =  new FileInputStream(file);

    }

    public static Float[] multiPolynomials(Polynomials a, Polynomials b ) {
        Float[] c = new Float[a.getMaxDegree() + b.getMaxDegree() + 1 ];
        return c ;
    }

    public static int getPolynomials(int a , int b ) {
        int r = a%b ;
        if (r == 0 ){
            return b ;
        }else if(r == 1 ) {
            return 1 ;
        }else{
            return getPolynomials(b,r) ;
        }
    }

    public static void main(String[] args) {
        System.out.println(getPolynomials(1231,123));
        System.out.println(getPolynomials(121,12));
        System.out.println(getPolynomials(121,11));
        System.out.println(getPolynomials(234,81));
    }
}
